FROM rust:1.99-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --locked --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home matrix \
    && mkdir /data && chown matrix:matrix /data
COPY --from=builder /build/target/release/matrix-ng-bridge /usr/local/bin/matrix-ng-bridge
USER matrix
VOLUME /data
EXPOSE 8099
ENTRYPOINT ["matrix-ng-bridge"]
