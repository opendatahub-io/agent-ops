# Builds the keycloak-registrar interceptor from source. Run from this directory:
#   oc new-build --binary --strategy=docker --name=keycloak-registrar
#   oc start-build keycloak-registrar --from-dir=. --follow
#
# The builder image is upstream Rust (community). Replace both stages with
# product-built images before customer or production use.
FROM docker.io/library/rust:1.95-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
COPY --from=build /src/target/release/keycloak-registrar-interceptor /usr/local/bin/keycloak-registrar-interceptor
USER 1001
ENTRYPOINT ["/usr/local/bin/keycloak-registrar-interceptor"]
