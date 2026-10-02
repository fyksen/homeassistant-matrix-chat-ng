mod api;
mod config;
mod events;
mod storage;

use std::{
    collections::HashSet,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

use anyhow::{Context, Result, ensure};
use matrix_sdk::{
    Client,
    authentication::matrix::MatrixSession,
    config::{RequestConfig, SyncSettings},
    ruma::UserId,
};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use tracing::{info, warn};

#[derive(Deserialize, Serialize)]
struct SavedSession {
    homeserver: String,
    session: MatrixSession,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| {
                "matrix_ng_bridge=info,matrix_sdk=error,matrix_sdk_crypto=error".into()
            }),
        )
        .init();
    let path = std::env::var_os("MATRIX_NG_CONFIG")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/run/secrets/matrix-config.json"));
    let mut config: config::Config =
        serde_json::from_slice(&std::fs::read(path).context("cannot read bridge configuration")?)
            .context("invalid bridge configuration JSON")?;
    config.validate()?;
    let _storage_lock = storage::prepare(&config.storage_path)?;
    let passphrase = storage::passphrase(&config.storage_path)?;
    let session_path = config.storage_path.join("session.json");
    let saved: Option<SavedSession> = match std::fs::read(&session_path) {
        Ok(bytes) => Some(
            serde_json::from_slice(&bytes)
                .context("invalid saved session; restore a storage backup")?,
        ),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e.into()),
    };
    let user_id = UserId::parse(&config.user_id)?;
    let mut builder = Client::builder()
        .sqlite_store(&config.storage_path, Some(&passphrase))
        .request_config(
            RequestConfig::new()
                .timeout(Duration::from_secs(30))
                .retry_limit(2),
        );
    if let Some(saved) = &saved {
        ensure!(
            saved.session.meta.user_id == user_id,
            "saved session belongs to a different user"
        );
        if let Some(homeserver) = &config.homeserver {
            ensure!(
                url::Url::parse(homeserver)? == url::Url::parse(&saved.homeserver)?,
                "saved session belongs to a different homeserver"
            );
        }
        builder = builder.homeserver_url(&saved.homeserver);
    } else if let Some(homeserver) = &config.homeserver {
        builder = builder.homeserver_url(homeserver);
    } else {
        builder = builder.server_name(user_id.server_name());
    }
    let client = builder
        .build()
        .await
        .context("cannot build Matrix client")?;
    let password = config.password.take();
    if let Some(saved) = saved {
        client
            .matrix_auth()
            .restore_session(saved.session, Default::default())
            .await
            .context("cannot restore Matrix session")?;
        info!("Restored persistent Matrix device");
    } else {
        client
            .matrix_auth()
            .login_username(
                &config.user_id,
                password
                    .as_deref()
                    .context("password is required for the first login")?,
            )
            .initial_device_display_name("Home Assistant Matrix NG")
            .send()
            .await
            .context("Matrix login failed")?;
        let session = client
            .matrix_auth()
            .session()
            .context("login returned no session")?;
        ensure!(
            session.meta.user_id == user_id,
            "login returned an unexpected Matrix user ID"
        );
        storage::write_private(
            &session_path,
            &serde_json::to_vec(&SavedSession {
                homeserver: client.homeserver().to_string(),
                session,
            })?,
        )?;
        info!("Created and saved persistent Matrix device");
    }
    drop(password);
    let response = client
        .sync_once(SyncSettings::default().timeout(Duration::from_secs(0)))
        .await
        .context("initial Matrix sync failed")?;
    let last_sync = Arc::new(AtomicU64::new(api::now()));
    let sync_client = client.clone();
    let sync_health = last_sync.clone();
    let sync_task = tokio::spawn(async move {
        let mut token = response.next_batch;
        loop {
            let settings = SyncSettings::default()
                .token(&token)
                .timeout(Duration::from_secs(20));
            match sync_client.sync_once(settings).await {
                Ok(response) => {
                    token = response.next_batch;
                    sync_health.store(api::now(), Ordering::Relaxed);
                }
                Err(_) => {
                    warn!("Matrix sync failed; retrying in 5 seconds");
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    });
    if let Some(key) = config.recovery_key.take().filter(|key| !key.is_empty()) {
        client
            .encryption()
            .recovery()
            .recover(&key)
            .await
            .context("Matrix recovery failed; check the key (identity will not be reset)")?;
        info!("Imported existing encryption identity from recovery storage");
    }
    // Query encryption state without joining rooms or accepting invitations.
    for id in &config.rooms {
        let room_id = matrix_sdk::ruma::RoomId::parse(id)?;
        if let Some(room) = client.get_room(&room_id) {
            room.request_encryption_state()
                .await
                .context("cannot query configured room encryption")?;
        }
    }
    let rooms = Arc::new(config.rooms.into_iter().collect::<HashSet<_>>());
    let events = events::EventHub::new(config.listen_for_commands);
    // Installed only after bootstrap/recovery: old timeline events cannot become commands.
    events::install(&client, events.clone(), rooms.clone());
    let state = api::AppState {
        client,
        rooms,
        events,
        last_sync,
        send_lock: Arc::new(Mutex::new(())),
    };
    let listener = tokio::net::TcpListener::bind(config.listen).await?;
    info!(listen = %config.listen, "Matrix NG bridge ready");
    axum::serve(listener, api::router(state, config.api_token))
        .with_graceful_shutdown(shutdown())
        .await?;
    sync_task.abort();
    Ok(())
}

async fn shutdown() {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                .expect("install SIGTERM handler");
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = terminate.recv() => {} }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
