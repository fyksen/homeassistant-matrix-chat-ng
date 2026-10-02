use std::{
    collections::HashSet,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Query, Request, State},
    http::{HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use matrix_sdk::{
    Client, EncryptionState, RoomState,
    ruma::{RoomId, events::room::message::RoomMessageEventContent},
};
use serde::Deserialize;
use serde_json::{Value, json};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;

pub const MAX_MESSAGE_BYTES: usize = 16_000;

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
    pub rooms: Arc<HashSet<String>>,
    pub last_sync: Arc<AtomicU64>,
    pub send_lock: Arc<Mutex<()>>,
    pub events: Arc<crate::events::EventHub>,
}

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

pub fn ready(state: &AppState) -> bool {
    let last = state.last_sync.load(Ordering::Relaxed);
    last != 0 && now().saturating_sub(last) < 120
}

pub fn router(state: AppState, token: String) -> Router {
    Router::new()
        .route("/v1/status", get(status))
        .route("/v1/send", post(send))
        .route("/v1/events", get(events))
        .layer(DefaultBodyLimit::max(64 * 1024))
        .route_layer(middleware::from_fn_with_state(Arc::new(token), authorize))
        .with_state(state)
}

async fn authorize(
    State(token): State<Arc<String>>,
    headers: HeaderMap,
    request: Request,
    next: Next,
) -> Response {
    let provided = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .unwrap_or("");
    if !bool::from(provided.as_bytes().ct_eq(token.as_bytes())) {
        return ApiError(StatusCode::UNAUTHORIZED, "invalid_auth").into_response();
    }
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert("cache-control", "no-store".parse().unwrap());
    response
}

struct ApiError(StatusCode, &'static str);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error": self.1}))).into_response()
    }
}

async fn status(State(state): State<AppState>) -> Json<Value> {
    let mut rooms = Vec::new();
    for id in state.rooms.iter() {
        let room_id = RoomId::parse(id).expect("validated configuration");
        if let Some(room) = state.client.get_room(&room_id) {
            rooms.push(json!({
                "room_id": id,
                "name": room.name().unwrap_or_else(|| id.clone()),
                "joined": room.state() == RoomState::Joined,
                "encrypted": matches!(room.encryption_state(), EncryptionState::Encrypted),
            }));
        } else {
            rooms.push(json!({"room_id": id, "name": id, "joined": false, "encrypted": false}));
        }
    }
    rooms.sort_by_key(|value| value["room_id"].as_str().unwrap().to_owned());
    let verified = state
        .client
        .encryption()
        .get_own_device()
        .await
        .ok()
        .flatten()
        .is_some_and(|device| device.is_verified_with_cross_signing());
    Json(json!({
        "protocol_version": 1,
        "ready": ready(&state),
        "user_id": state.client.user_id().map(ToString::to_string),
        "device_id": state.client.device_id().map(ToString::to_string),
        "device_verified": verified,
        "commands_enabled": state.events.enabled,
        "rooms": rooms,
    }))
}

async fn events(
    State(state): State<AppState>,
    Query(query): Query<crate::events::EventsQuery>,
) -> Result<Json<crate::events::EventBatch>, ApiError> {
    if !state.events.enabled {
        return Err(ApiError(StatusCode::CONFLICT, "commands_disabled"));
    }
    if !state.rooms.contains(&query.room_id) {
        return Err(ApiError(StatusCode::FORBIDDEN, "room_not_allowed"));
    }
    Ok(Json(state.events.poll(&query).await))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SendRequest {
    room_id: String,
    message: String,
    title: Option<String>,
    transaction_id: Option<String>,
}

fn body(request: &SendRequest) -> Result<String, ApiError> {
    if request.message.trim().is_empty() {
        return Err(ApiError(StatusCode::BAD_REQUEST, "empty_message"));
    }
    let body = match request.title.as_deref().filter(|title| !title.is_empty()) {
        Some(title) => format!("{title}\n\n{}", request.message),
        None => request.message.clone(),
    };
    if body.len() > MAX_MESSAGE_BYTES {
        return Err(ApiError(StatusCode::BAD_REQUEST, "message_too_large"));
    }
    if request.transaction_id.as_ref().is_some_and(|id| {
        id.is_empty()
            || id.len() > 128
            || !id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    }) {
        return Err(ApiError(StatusCode::BAD_REQUEST, "invalid_transaction_id"));
    }
    Ok(body)
}

async fn send(
    State(state): State<AppState>,
    Json(request): Json<SendRequest>,
) -> Result<Json<Value>, ApiError> {
    let body = body(&request)?;
    if !state.rooms.contains(&request.room_id) {
        return Err(ApiError(StatusCode::FORBIDDEN, "room_not_allowed"));
    }
    if !ready(&state) {
        return Err(ApiError(StatusCode::SERVICE_UNAVAILABLE, "not_ready"));
    }
    let room_id = RoomId::parse(&request.room_id)
        .map_err(|_| ApiError(StatusCode::BAD_REQUEST, "invalid_room_id"))?;
    let room = state
        .client
        .get_room(&room_id)
        .filter(|room| room.state() == RoomState::Joined)
        .ok_or(ApiError(StatusCode::CONFLICT, "room_not_joined"))?;
    let result = tokio::time::timeout(Duration::from_secs(60), async {
        let _guard = state.send_lock.lock().await;
        let encryption = room
            .latest_encryption_state()
            .await
            .map_err(|_| ApiError(StatusCode::BAD_GATEWAY, "encryption_state_unavailable"))?;
        // Fail closed. Unknown or plaintext rooms must NEVER receive a message.
        if !matches!(encryption, EncryptionState::Encrypted) {
            return Err(ApiError(StatusCode::CONFLICT, "room_not_encrypted"));
        }
        let transaction_id = request
            .transaction_id
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let response = room
            .send(RoomMessageEventContent::text_plain(body))
            .with_transaction_id(transaction_id.into())
            .await
            .map_err(|_| ApiError(StatusCode::BAD_GATEWAY, "matrix_send_failed"))?;
        if response.encryption_info.is_none() {
            return Err(ApiError(
                StatusCode::BAD_GATEWAY,
                "encryption_not_confirmed",
            ));
        }
        Ok(Json(
            json!({"event_id": response.response.event_id, "encrypted": true}),
        ))
    })
    .await;
    result.map_err(|_| ApiError(StatusCode::GATEWAY_TIMEOUT, "send_timeout"))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use tower::ServiceExt;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{method, path, path_regex},
    };

    async fn joined_plaintext_room() -> (AppState, MockServer) {
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
            .and(path_regex(r"/rooms/.*/state/m\.room\.encryption/?$"))
            .respond_with(ResponseTemplate::new(404).set_body_json(json!({
                "errcode": "M_NOT_FOUND", "error": "No encryption state event"
            })))
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/_matrix/client/v3/sync"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({
                "next_batch": "sync1",
                "rooms": {"join": {"!plain:example.org": {
                    "timeline": {"events": [], "limited": false, "prev_batch": "t1"},
                    "state": {"events": [
                        {"type": "m.room.create", "state_key": "", "sender": "@bot:example.org", "event_id": "$create", "origin_server_ts": 1, "content": {"creator": "@bot:example.org", "room_version": "10"}},
                        {"type": "m.room.member", "state_key": "@bot:example.org", "sender": "@bot:example.org", "event_id": "$join", "origin_server_ts": 2, "content": {"membership": "join"}}
                    ]}
                }}}
            })))
            .mount(&server).await;
        let client = Client::builder()
            .homeserver_url(server.uri())
            .build()
            .await
            .unwrap();
        let session = serde_json::from_value(json!({
            "user_id": "@bot:example.org", "device_id": "TESTDEVICE", "access_token": "test-token"
        }))
        .unwrap();
        client
            .matrix_auth()
            .restore_session(session, Default::default())
            .await
            .unwrap();
        client
            .sync_once(matrix_sdk::config::SyncSettings::default())
            .await
            .unwrap();
        let state = AppState {
            client,
            rooms: Arc::new(HashSet::from([
                "!plain:example.org".into(),
                "!missing:example.org".into(),
            ])),
            last_sync: Arc::new(AtomicU64::new(now())),
            send_lock: Arc::new(Mutex::new(())),
            events: crate::events::EventHub::new(false),
        };
        (state, server)
    }

    #[tokio::test]
    async fn rejects_plaintext_unjoined_and_disallowed_rooms_without_sending() {
        let (state, server) = joined_plaintext_room().await;
        let app = router(state, "a".repeat(32));
        for (room_id, expected) in [
            ("!plain:example.org", "room_not_encrypted"),
            ("!missing:example.org", "room_not_joined"),
            ("!other:example.org", "room_not_allowed"),
        ] {
            let request = Request::builder()
                .method("POST")
                .uri("/v1/send")
                .header("authorization", format!("Bearer {}", "a".repeat(32)))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"room_id": room_id, "message": "must never be uploaded"}).to_string(),
                ))
                .unwrap();
            let response = app.clone().oneshot(request).await.unwrap();
            assert!(response.status().is_client_error());
            let bytes = axum::body::to_bytes(response.into_body(), 1024)
                .await
                .unwrap();
            assert_eq!(
                serde_json::from_slice::<Value>(&bytes).unwrap()["error"],
                expected
            );
        }
        assert!(
            !server
                .received_requests()
                .await
                .unwrap()
                .iter()
                .any(|r| r.url.path().contains("/send/"))
        );
    }

    #[tokio::test]
    async fn rejects_stale_sync_without_sending() {
        let (state, server) = joined_plaintext_room().await;
        state.last_sync.store(now() - 121, Ordering::Relaxed);
        assert!(!ready(&state));
        let app = router(state, "a".repeat(32));
        let request = Request::builder()
            .method("POST")
            .uri("/v1/send")
            .header("authorization", format!("Bearer {}", "a".repeat(32)))
            .header("content-type", "application/json")
            .body(Body::from(
                json!({"room_id": "!plain:example.org", "message": "must not send"}).to_string(),
            ))
            .unwrap();
        assert_eq!(
            app.oneshot(request).await.unwrap().status(),
            StatusCode::SERVICE_UNAVAILABLE
        );
        assert!(
            !server
                .received_requests()
                .await
                .unwrap()
                .iter()
                .any(|r| r.url.path().contains("/send/"))
        );
    }

    #[tokio::test]
    async fn command_stream_requires_auth_opt_in_and_room_allowlisting() {
        let (mut state, _server) = joined_plaintext_room().await;
        let token = "a".repeat(32);
        let app = router(state.clone(), token.clone());
        let unauthorized = Request::builder()
            .uri("/v1/events?room_id=!plain:example.org&wait=0")
            .body(Body::empty())
            .unwrap();
        assert_eq!(
            app.clone().oneshot(unauthorized).await.unwrap().status(),
            StatusCode::UNAUTHORIZED
        );
        let disabled = Request::builder()
            .uri("/v1/events?room_id=!plain:example.org&wait=0")
            .header("authorization", format!("Bearer {token}"))
            .body(Body::empty())
            .unwrap();
        assert_eq!(
            app.oneshot(disabled).await.unwrap().status(),
            StatusCode::CONFLICT
        );
        state.events = crate::events::EventHub::new(true);
        let app = router(state, token.clone());
        for (room, status) in [
            ("!other:example.org", StatusCode::FORBIDDEN),
            ("!plain:example.org", StatusCode::OK),
        ] {
            let request = Request::builder()
                .uri(format!("/v1/events?room_id={room}&wait=0"))
                .header("authorization", format!("Bearer {token}"))
                .body(Body::empty())
                .unwrap();
            let response = app.clone().oneshot(request).await.unwrap();
            assert_eq!(response.status(), status);
            assert_eq!(response.headers()["cache-control"], "no-store");
        }
    }

    #[test]
    fn validates_body_and_transaction_id() {
        let mut request = SendRequest {
            room_id: "!room".into(),
            message: "hello".into(),
            title: Some("Title".into()),
            transaction_id: None,
        };
        assert_eq!(body(&request).ok().unwrap(), "Title\n\nhello");
        request.message = " ".into();
        assert!(body(&request).is_err());
        request.message = "ø".repeat(MAX_MESSAGE_BYTES);
        assert!(body(&request).is_err());
        request.message = "valid".into();
        request.transaction_id = Some("invalid/id".into());
        assert!(body(&request).is_err());
    }

    #[tokio::test]
    async fn requires_exact_bearer_token() {
        let token = "a".repeat(32);
        let app = Router::new()
            .route("/", get(|| async { "ok" }))
            .route_layer(middleware::from_fn_with_state(
                Arc::new(token.clone()),
                authorize,
            ));
        for provided in [
            None,
            Some("Bearer wrong".to_owned()),
            Some(format!("Basic {token}")),
            Some(format!("Bearer {token}")),
        ] {
            let mut request = Request::builder().uri("/");
            let valid = provided.as_ref() == Some(&format!("Bearer {token}"));
            if let Some(value) = provided {
                request = request.header("authorization", value);
            }
            let response = app
                .clone()
                .oneshot(request.body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(
                response.status(),
                if valid {
                    StatusCode::OK
                } else {
                    StatusCode::UNAUTHORIZED
                }
            );
        }
    }
}
