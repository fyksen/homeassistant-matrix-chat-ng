FROM rust:1.99-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src
RUN cargo build --locked --release

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home matrix \
    && mkdir /data && chown matrix:matrix /data
COPY --from=builder /build/target/release/matrix-ng-bridge /usr/local/bin/matrix-ng-bridge
USER matrix
VOLUME /data
EXPOSE 8099
ENTRYPOINT ["matrix-ng-bridge"]
