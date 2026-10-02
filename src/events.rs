//! Bounded, ephemeral incoming-event stream. Never stores decrypted room content on disk.

use std::{
    collections::{HashSet, VecDeque},
    sync::{Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use matrix_sdk::{
    Client, EncryptionState, Room, RoomState,
    deserialized_responses::EncryptionInfo,
    event_handler::RawEvent,
    ruma::events::{
        reaction::OriginalSyncReactionEvent,
        room::message::{MessageType, OriginalSyncRoomMessageEvent},
    },
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::Notify;

const CAPACITY: usize = 1024;
pub const MAX_AGE_MS: u64 = 60_000;

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Clone, Serialize)]
pub struct IncomingEvent {
    pub sequence: u64,
    pub kind: &'static str,
    pub room_id: String,
    pub sender: String,
    /// Message event ID for messages; reacted-to message ID for reactions.
    pub event_id: String,
    pub source_event_id: String,
    pub thread_parent: String,
    pub timestamp: u64,
    pub encrypted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub body: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reaction: Option<String>,
}

#[derive(Default)]
struct Buffer {
    sequence: u64,
    events: VecDeque<IncomingEvent>,
    seen: HashSet<String>,
    seen_order: VecDeque<String>,
}

pub struct EventHub {
    pub enabled: bool,
    epoch: String,
    buffer: Mutex<Buffer>,
    notify: Notify,
}

#[derive(Deserialize)]
pub struct EventsQuery {
    pub room_id: String,
    pub cursor: Option<String>,
    #[serde(default = "default_wait")]
    pub wait: u64,
}

fn default_wait() -> u64 {
    20
}

#[derive(Serialize)]
pub struct EventBatch {
    pub cursor: String,
    /// A fresh subscription, bridge restart or queue gap skips history, intentionally.
    pub reset: bool,
    pub events: Vec<IncomingEvent>,
}

impl EventHub {
    pub fn new(enabled: bool) -> Arc<Self> {
        Arc::new(Self {
            enabled,
            epoch: uuid::Uuid::new_v4().to_string(),
            buffer: Mutex::new(Buffer::default()),
            notify: Notify::new(),
        })
    }

    pub fn publish(&self, mut event: IncomingEvent) {
        let now = now_ms();
        if !self.enabled
            || now.saturating_sub(event.timestamp) > MAX_AGE_MS
            || event.timestamp > now + 30_000
            || (event.kind == "message" && !event.encrypted)
            || event
                .body
                .as_ref()
                .is_some_and(|body| body.len() > crate::api::MAX_MESSAGE_BYTES)
        {
            return;
        }
        let mut buffer = self.buffer.lock().unwrap();
        if !buffer.seen.insert(event.source_event_id.clone()) {
            return;
        }
        buffer.seen_order.push_back(event.source_event_id.clone());
        while buffer.seen_order.len() > CAPACITY * 4 {
            let old = buffer.seen_order.pop_front().unwrap();
            buffer.seen.remove(&old);
        }
        buffer.sequence += 1;
        event.sequence = buffer.sequence;
        buffer.events.push_back(event);
        while buffer.events.len() > CAPACITY
            || buffer
                .events
                .front()
                .is_some_and(|event| now.saturating_sub(event.timestamp) > MAX_AGE_MS)
        {
            buffer.events.pop_front();
        }
        drop(buffer);
        self.notify.notify_waiters();
    }

    fn snapshot(&self, room_id: &str, cursor: Option<&str>) -> EventBatch {
        let buffer = self.buffer.lock().unwrap();
        let since = cursor
            .and_then(|cursor| cursor.split_once(':'))
            .filter(|(epoch, _)| *epoch == self.epoch)
            .and_then(|(_, sequence)| sequence.parse::<u64>().ok());
        let reset = since.is_none_or(|since| {
            since > buffer.sequence
                || buffer
                    .events
                    .front()
                    .is_some_and(|event| since.saturating_add(1) < event.sequence)
        });
        let events = if reset {
            Vec::new()
        } else {
            buffer
                .events
                .iter()
                .filter(|event| {
                    event.sequence > since.unwrap()
                        && event.room_id == room_id
                        && now_ms().saturating_sub(event.timestamp) <= MAX_AGE_MS
                })
                .cloned()
                .collect()
        };
        EventBatch {
            cursor: format!("{}:{}", self.epoch, buffer.sequence),
            reset,
            events,
        }
    }

    pub async fn poll(&self, query: &EventsQuery) -> EventBatch {
        // Register the waiter before reading the buffer, avoiding lost wakeups.
        let notified = self.notify.notified();
        tokio::pin!(notified);
        notified.as_mut().enable();
        let batch = self.snapshot(&query.room_id, query.cursor.as_deref());
        if batch.reset || Some(batch.cursor.as_str()) != query.cursor.as_deref() || query.wait == 0
        {
            return batch;
        }
        let _ = tokio::time::timeout(Duration::from_secs(query.wait.min(20)), notified).await;
        self.snapshot(&query.room_id, query.cursor.as_deref())
    }
}

fn allowed(room: &Room, sender: &str, rooms: &HashSet<String>) -> bool {
    room.state() == RoomState::Joined
        && rooms.contains(room.room_id().as_str())
        && sender != room.own_user_id().as_str()
        && matches!(room.encryption_state(), EncryptionState::Encrypted)
}

fn thread_parent(raw: &Value, fallback: &str) -> String {
    let relation = &raw["content"]["m.relates_to"];
    if relation["rel_type"] == "m.thread" {
        relation["event_id"].as_str().unwrap_or(fallback).to_owned()
    } else {
        fallback.to_owned()
    }
}

pub fn install(client: &Client, hub: Arc<EventHub>, rooms: Arc<HashSet<String>>) {
    if !hub.enabled {
        return;
    }
    let message_hub = hub.clone();
    let message_rooms = rooms.clone();
    client.add_event_handler(
        move |event: OriginalSyncRoomMessageEvent,
              room: Room,
              encryption: Option<EncryptionInfo>,
              raw: RawEvent| {
            let hub = message_hub.clone();
            let rooms = message_rooms.clone();
            async move {
                if encryption.is_none() || !allowed(&room, event.sender.as_str(), &rooms) {
                    return;
                }
                let MessageType::Text(text) = &event.content.msgtype else {
                    return;
                };
                let Ok(raw) = serde_json::from_str::<Value>(raw.get()) else {
                    return;
                };
                // Edits must not turn an old message into a fresh command.
                if raw["content"]["m.relates_to"]["rel_type"] == "m.replace" {
                    return;
                }
                let id = event.event_id.to_string();
                hub.publish(IncomingEvent {
                    sequence: 0,
                    kind: "message",
                    room_id: room.room_id().to_string(),
                    sender: event.sender.to_string(),
                    event_id: id.clone(),
                    source_event_id: id.clone(),
                    thread_parent: thread_parent(&raw, &id),
                    timestamp: event.origin_server_ts.get().into(),
                    encrypted: true,
                    body: Some(text.body.clone()),
                    reaction: None,
                });
            }
        },
    );
    client.add_event_handler(move |event: OriginalSyncReactionEvent, room: Room| {
        let hub = hub.clone();
        let rooms = rooms.clone();
        async move {
            if !allowed(&room, event.sender.as_str(), &rooms) {
                return;
            }
            let target = event.content.relates_to.event_id.to_string();
            let parent = match tokio::time::timeout(
                Duration::from_secs(5),
                room.event(&event.content.relates_to.event_id, None),
            )
            .await
            {
                Ok(Ok(event)) => serde_json::from_str::<Value>(event.raw().json().get())
                    .map(|raw| thread_parent(&raw, &target))
                    .unwrap_or_else(|_| target.clone()),
                _ => target.clone(),
            };
            // Matrix reactions are normally plaintext, even in encrypted rooms.
            hub.publish(IncomingEvent {
                sequence: 0,
                kind: "reaction",
                room_id: room.room_id().to_string(),
                sender: event.sender.to_string(),
                event_id: target,
                source_event_id: event.event_id.to_string(),
                thread_parent: parent,
                timestamp: event.origin_server_ts.get().into(),
                encrypted: false,
                body: None,
                reaction: Some(event.content.relates_to.key.clone()),
            });
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{method, path, path_regex},
    };

    fn event(id: &str) -> IncomingEvent {
        IncomingEvent {
            sequence: 0,
            kind: "message",
            room_id: "!room".into(),
            sender: "@alice:example.org".into(),
            event_id: id.into(),
            source_event_id: id.into(),
            thread_parent: id.into(),
            timestamp: now_ms(),
            encrypted: true,
            body: Some("!test".into()),
            reaction: None,
        }
    }

    #[tokio::test]
    async fn skips_initial_history_and_deduplicates() {
        let hub = EventHub::new(true);
        hub.publish(event("$old"));
        let mut query = EventsQuery {
            room_id: "!room".into(),
            cursor: None,
            wait: 0,
        };
        let initial = hub.poll(&query).await;
        assert!(initial.reset && initial.events.is_empty());
        query.cursor = Some(initial.cursor);
        hub.publish(event("$new"));
        hub.publish(event("$new"));
        assert_eq!(hub.poll(&query).await.events.len(), 1);
    }

    #[tokio::test]
    async fn rejects_plaintext_stale_and_oversized_messages() {
        let hub = EventHub::new(true);
        let mut event = event("$bad");
        event.encrypted = false;
        hub.publish(event.clone());
        event.encrypted = true;
        event.timestamp = now_ms() - MAX_AGE_MS - 1;
        hub.publish(event.clone());
        event.timestamp = now_ms();
        event.body = Some("x".repeat(crate::api::MAX_MESSAGE_BYTES + 1));
        hub.publish(event);
        assert_eq!(hub.buffer.lock().unwrap().sequence, 0);
    }

    #[tokio::test]
    async fn resets_after_overflow_or_bridge_restart_and_filters_rooms() {
        let hub = EventHub::new(true);
        let mut query = EventsQuery {
            room_id: "!room".into(),
            cursor: Some(hub.snapshot("!room", None).cursor),
            wait: 0,
        };
        for i in 0..=CAPACITY {
            hub.publish(event(&format!("${i}")));
        }
        assert!(hub.poll(&query).await.reset);
        query.cursor = Some(hub.snapshot("!room", None).cursor);
        hub.publish(event("$next"));
        query.room_id = "!other".into();
        assert!(hub.poll(&query).await.events.is_empty());
        assert!(EventHub::new(true).poll(&query).await.reset);
    }

    #[test]
    fn extracts_thread_parent() {
        let raw = serde_json::json!({"content": {"m.relates_to": {"rel_type": "m.thread", "event_id": "$root"}}});
        assert_eq!(thread_parent(&raw, "$event"), "$root");
        assert_eq!(thread_parent(&Value::Null, "$event"), "$event");
    }

    #[tokio::test]
    async fn sdk_decrypts_incoming_command_and_rejects_plaintext_and_self_messages() {
        use matrix_sdk::encryption::vodozemac::{
            Curve25519PublicKey,
            megolm::{GroupSession, InboundGroupSession, SessionConfig},
        };
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/_matrix/client/versions"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"versions": ["v1.15"]})))
            .mount(&server)
            .await;
        Mock::given(method("POST"))
            .and(path("/_matrix/client/v3/keys/upload"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(json!({"one_time_key_counts": {"signed_curve25519": 50}})),
            )
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path_regex(r"/state/m\.room\.encryption/?$"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(json!({"algorithm": "m.megolm.v1.aes-sha2"})),
            )
            .mount(&server)
            .await;
        let mut initial = json!({"next_batch": "s1", "rooms": {"join": {"!room:example.org": {
            "timeline": {"events": [], "limited": false, "prev_batch": "t0"},
            "state": {"events": [
                {"type": "m.room.create", "state_key": "", "sender": "@bot:example.org", "event_id": "$create", "origin_server_ts": 1, "content": {"creator": "@bot:example.org", "room_version": "10"}},
                {"type": "m.room.member", "state_key": "@bot:example.org", "sender": "@bot:example.org", "event_id": "$join", "origin_server_ts": 2, "content": {"membership": "join"}},
                {"type": "m.room.encryption", "state_key": "", "sender": "@bot:example.org", "event_id": "$encryption", "origin_server_ts": 3, "content": {"algorithm": "m.megolm.v1.aes-sha2"}}
            ]}
        }}}});
        let sync = Mock::given(method("GET"))
            .and(path("/_matrix/client/v3/sync"))
            .respond_with(ResponseTemplate::new(200).set_body_json(initial.clone()))
            .mount_as_scoped(&server)
            .await;
        let client = Client::builder()
            .homeserver_url(server.uri())
            .build()
            .await
            .unwrap();
        client.matrix_auth().restore_session(serde_json::from_value(json!({"user_id": "@bot:example.org", "device_id": "BOT", "access_token": "test"})).unwrap(), Default::default()).await.unwrap();
        client
            .sync_once(matrix_sdk::config::SyncSettings::default())
            .await
            .unwrap();
        client
            .get_room(&matrix_sdk::ruma::RoomId::parse("!room:example.org").unwrap())
            .unwrap()
            .request_encryption_state()
            .await
            .unwrap();
        drop(sync);

        let mut outbound = GroupSession::new(SessionConfig::version_1());
        let inbound = InboundGroupSession::new(&outbound.session_key(), SessionConfig::version_1());
        let sender_key = Curve25519PublicKey::from_bytes([42; 32]).to_base64();
        let key = serde_json::from_value(json!({
            "algorithm": "m.megolm.v1.aes-sha2", "room_id": "!room:example.org", "sender_key": sender_key,
            "session_id": outbound.session_id(), "session_key": inbound.export_at_first_known_index().to_base64(),
            "sender_claimed_keys": {"ed25519": outbound.session_id()}, "forwarding_curve25519_key_chain": []
        })).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let key_file = directory.path().join("test-keys");
        std::fs::write(
            &key_file,
            matrix_sdk_crypto::encrypt_room_key_export(&[key], "test", 1).unwrap(),
        )
        .unwrap();
        client
            .encryption()
            .import_room_keys(key_file, "test")
            .await
            .unwrap();
        let hub = EventHub::new(true);
        install(
            &client,
            hub.clone(),
            Arc::new(HashSet::from(["!room:example.org".into()])),
        );
        let cursor = hub.snapshot("!room:example.org", None).cursor;
        let content = json!({"room_id": "!room:example.org", "type": "m.room.message", "sender": "@alice:example.org", "content": {
            "msgtype": "m.text", "body": "!test encrypted argument", "m.relates_to": {"rel_type": "m.thread", "event_id": "$root"}
        }});
        let encrypted = json!({"type": "m.room.encrypted", "event_id": "$incoming", "sender": "@alice:example.org", "origin_server_ts": now_ms(), "content": {
            "algorithm": "m.megolm.v1.aes-sha2", "sender_key": sender_key, "session_id": outbound.session_id(),
            "device_id": "ALICE", "ciphertext": outbound.encrypt(content.to_string()).to_base64()
        }});
        Mock::given(method("GET"))
            .and(path_regex(r"/event/.*incoming$"))
            .respond_with(ResponseTemplate::new(200).set_body_json(encrypted.clone()))
            .mount(&server)
            .await;
        initial["next_batch"] = json!("s2");
        initial["rooms"]["join"]["!room:example.org"]["timeline"]["events"] = json!([
            encrypted,
            {"type": "m.reaction", "event_id": "$reaction", "sender": "@alice:example.org", "origin_server_ts": now_ms(), "content": {"m.relates_to": {"rel_type": "m.annotation", "event_id": "$incoming", "key": "👍"}}},
            {"type": "m.room.message", "event_id": "$plaintext", "sender": "@alice:example.org", "origin_server_ts": now_ms(), "content": {"msgtype": "m.text", "body": "!test plaintext"}},
            {"type": "m.room.message", "event_id": "$own", "sender": "@bot:example.org", "origin_server_ts": now_ms(), "content": {"msgtype": "m.text", "body": "!test self"}}
        ]);
        Mock::given(method("GET"))
            .and(path("/_matrix/client/v3/sync"))
            .respond_with(ResponseTemplate::new(200).set_body_json(initial))
            .mount(&server)
            .await;
        client
            .sync_once(matrix_sdk::config::SyncSettings::default().token("s1"))
            .await
            .unwrap();
        let batch = hub
            .poll(&EventsQuery {
                room_id: "!room:example.org".into(),
                cursor: Some(cursor),
                wait: 0,
            })
            .await;
        assert_eq!(
            batch.events.len(),
            2,
            "only a decrypted command and a reaction should be emitted"
        );
        let message = batch
            .events
            .iter()
            .find(|event| event.kind == "message")
            .unwrap();
        assert_eq!(message.body.as_deref(), Some("!test encrypted argument"));
        assert_eq!(message.thread_parent, "$root");
        assert!(message.encrypted);
        let reaction = batch
            .events
            .iter()
            .find(|event| event.kind == "reaction")
            .unwrap();
        assert_eq!(reaction.event_id, "$incoming");
        assert_eq!(reaction.source_event_id, "$reaction");
        assert_eq!(reaction.reaction.as_deref(), Some("👍"));
        assert_eq!(reaction.thread_parent, "$root");
        assert!(!reaction.encrypted);
    }
}
