use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::Path,
};

use anyhow::{Context, Result};
use fs2::FileExt;
use uuid::Uuid;

pub fn prepare(path: &Path) -> Result<File> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    let lock = private_options()
        .read(true)
        .create(true)
        .truncate(false)
        .open(path.join("bridge.lock"))?;
    lock.try_lock_exclusive()
        .context("another bridge is using this storage directory")?;
    Ok(lock)
}

fn private_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    options.write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
}

/// Atomic replacement prevents a crash from leaving a partially written session.
pub fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension(format!("tmp-{}", Uuid::new_v4()));
    let mut file = private_options().create_new(true).open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    if let Some(parent) = path.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

pub fn passphrase(directory: &Path) -> Result<String> {
    let path = directory.join("store-passphrase");
    match fs::read_to_string(&path) {
        Ok(value) => Ok(value),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let value = format!("{}{}", Uuid::new_v4(), Uuid::new_v4());
            write_private(&path, value.as_bytes())?;
            Ok(value)
        }
        Err(e) => Err(e.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persists_passphrase_and_excludes_other_processes() {
        let directory = tempfile::tempdir().unwrap();
        let _lock = prepare(directory.path()).unwrap();
        assert!(prepare(directory.path()).is_err());
        assert_eq!(
            passphrase(directory.path()).unwrap(),
            passphrase(directory.path()).unwrap()
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(directory.path().join("store-passphrase"))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }

    #[test]
    fn atomically_replaces_session() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("session.json");
        write_private(&path, b"first").unwrap();
        write_private(&path, b"second").unwrap();
        assert_eq!(fs::read(path).unwrap(), b"second");
    }
}
