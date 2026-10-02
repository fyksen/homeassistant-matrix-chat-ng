# Changelog

## 0.3.1

- Clearer logs: expected Matrix SDK "Account data not found" probes are no longer
  shown as errors, startup no longer prints a misleading "waiting" message, and the
  log now states whether the app was stopped by Home Assistant or the bridge exited.

## 0.3.0

- Supervisor app with prebuilt amd64/aarch64 images, persistent encryption storage,
  cold backups, watchdog, automatic discovery and connection-token provisioning.
- UI-based command configuration in the Matrix NG integration.
