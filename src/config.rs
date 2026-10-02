use std::{net::SocketAddr, path::PathBuf};

use anyhow::{Context, Result, ensure};
use matrix_sdk::ruma::{RoomId, UserId};
use serde::Deserialize;
use url::Url;

/// Deliberately not Debug: this struct contains credentials.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub homeserver: Option<String>,
    pub user_id: String,
    pub password: Option<String>,
    pub recovery_key: Option<String>,
    pub api_token: String,
    pub rooms: Vec<String>,
    /// Receiving room content is opt-in; notification-only deployments remain unchanged.
    #[serde(default)]
    pub listen_for_commands: bool,
    #[serde(default = "default_storage")]
    pub storage_path: PathBuf,
    #[serde(default = "default_listen")]
    pub listen: SocketAddr,
}

fn default_storage() -> PathBuf {
    PathBuf::from("/data")
}

fn default_listen() -> SocketAddr {
    "127.0.0.1:8099".parse().unwrap()
}

impl Config {
    pub fn validate(&self) -> Result<()> {
        UserId::parse(&self.user_id).context("user_id must be a full Matrix ID")?;
        ensure!(
            self.api_token.len() >= 32 && self.api_token.bytes().all(|c| c.is_ascii_graphic()),
            "api_token must contain at least 32 printable ASCII characters without spaces"
        );
        ensure!(!self.rooms.is_empty(), "rooms must not be empty");
        for room in &self.rooms {
            RoomId::parse(room).context("rooms must contain internal Matrix room IDs")?;
        }
        if let Some(homeserver) = &self.homeserver {
            let url = Url::parse(homeserver).context("invalid homeserver URL")?;
            ensure!(
                url.scheme() == "https" && url.host_str().is_some(),
                "homeserver must use HTTPS"
            );
            ensure!(
                url.username().is_empty() && url.password().is_none(),
                "homeserver URL must not contain credentials"
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn config() -> Config {
        serde_json::from_value(json!({
            "user_id": "@bot:example.org",
            "api_token": "a".repeat(32),
            "rooms": ["!opaque_v12_room_id"]
        }))
        .unwrap()
    }

    #[test]
    fn accepts_serverless_v12_room_ids() {
        config().validate().unwrap();
    }

    #[test]
    fn rejects_weak_tokens_empty_rooms_and_http() {
        let mut c = config();
        c.api_token = "short".into();
        assert!(c.validate().is_err());
        let mut c = config();
        c.rooms.clear();
        assert!(c.validate().is_err());
        let mut c = config();
        c.homeserver = Some("http://example.org".into());
        assert!(c.validate().is_err());
    }
}
