//! Decrypt a sent event from an existing bridge store. Stop the bridge first.
//! Usage: MATRIX_NG_CONFIG=... cargo run --example verify_event -- ROOM_ID EVENT_ID

use std::path::PathBuf;

use anyhow::{Context, Result, ensure};
use matrix_sdk::{
    Client,
    authentication::matrix::MatrixSession,
    ruma::{EventId, RoomId},
};
use serde::Deserialize;
use serde_json::Value;

#[path = "../src/storage.rs"]
mod storage;

#[derive(Deserialize)]
struct SavedSession {
    homeserver: String,
    session: MatrixSession,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    ensure!(args.len() == 3, "usage: verify_event ROOM_ID EVENT_ID");
    let config: Value =
        serde_json::from_slice(&std::fs::read(std::env::var("MATRIX_NG_CONFIG")?)?)?;
    let path = PathBuf::from(
        config["storage_path"]
            .as_str()
            .context("missing storage_path")?,
    );
    let _lock = storage::prepare(&path)?;
    let saved: SavedSession = serde_json::from_slice(&std::fs::read(path.join("session.json"))?)?;
    let client = Client::builder()
        .homeserver_url(saved.homeserver)
        .sqlite_store(&path, Some(&storage::passphrase(&path)?))
        .build()
        .await?;
    client
        .matrix_auth()
        .restore_session(saved.session, Default::default())
        .await?;
    let room = client
        .get_room(&RoomId::parse(&args[1])?)
        .context("room not cached")?;
    let event = room.event(&EventId::parse(&args[2])?, None).await?;
    ensure!(
        event.encryption_info().is_some(),
        "event was not successfully decrypted"
    );
    let content: Value = serde_json::from_str(event.raw().json().get())?;
    ensure!(
        content["type"] == "m.room.message",
        "unexpected decrypted event type"
    );
    println!(
        "Successfully decrypted an encrypted Matrix event with the persistent Rust SDK crypto store."
    );
    println!(
        "{}",
        content["content"]["body"]
            .as_str()
            .context("message has no body")?
    );
    Ok(())
}
