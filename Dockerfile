FROM rust:1.99-bookworm@sha256:fbc3a359627c6b5d9c8b20aae5c413a87392954f020006d7a9f7d95938964b23 AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src
RUN cargo build --locked --release

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 matrix \
    && useradd --system --uid 10001 --gid matrix --create-home matrix \
    && mkdir /data && chown matrix:matrix /data
COPY --from=builder /build/target/release/matrix-ng-bridge /usr/local/bin/matrix-ng-bridge
ARG BUILD_VERSION=0.3.0
LABEL org.opencontainers.image.source="https://github.com/fyksen/homeassistant-matrix-chat-ng" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${BUILD_VERSION}"

FROM runtime AS supervisor_app
RUN apt-get update && apt-get install -y --no-install-recommends python3 gosu \
    && rm -rf /var/lib/apt/lists/*
ARG BUILD_VERSION=0.3.0
ARG BUILD_ARCH=amd64
LABEL io.hass.type="app" io.hass.version="${BUILD_VERSION}" io.hass.arch="${BUILD_ARCH}"
COPY apps/matrix_ng_bridge/startup.py /usr/local/bin/matrix-ng-supervisor
ENTRYPOINT ["python3", "/usr/local/bin/matrix-ng-supervisor"]
EXPOSE 8099

# Keep the ordinary Docker/Compose image non-root and backwards compatible.
FROM runtime AS bridge
USER matrix
VOLUME /data
EXPOSE 8099
ENTRYPOINT ["matrix-ng-bridge"]
