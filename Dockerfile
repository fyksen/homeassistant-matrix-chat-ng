FROM rust:1.99-bookworm@sha256:59037199c44290f2befcdd58dcc540164763fc296950255aaefeef096a1866b0 AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src
RUN cargo build --locked --release

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 matrix \
    && useradd --system --uid 10001 --gid matrix --create-home matrix \
    && mkdir /data && chown matrix:matrix /data
COPY --from=builder /build/target/release/matrix-ng-bridge /usr/local/bin/matrix-ng-bridge
ARG BUILD_VERSION=0.3.17
LABEL org.opencontainers.image.source="https://github.com/fyksen/homeassistant-matrix-chat-ng" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${BUILD_VERSION}"

FROM runtime AS supervisor_app
RUN apt-get update && apt-get install -y --no-install-recommends python3 gosu \
    && rm -rf /var/lib/apt/lists/*
ARG BUILD_VERSION=0.3.17
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
