// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

use std::sync::Arc;

use keycloak_registrar_interceptor::{
    auth::GatewayTokenVerifier, config::Config, keycloak::KeycloakAdmin, service::RegistrarService,
    spiffe::SpiffeIdTemplate,
};
use openshell_core::proto::gateway_interceptor::v1::gateway_interceptor_server::GatewayInterceptorServer;
use tonic::transport::{Identity, Server, ServerTlsConfig};
use tracing::info;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;

    let config = Config::from_env(|key| std::env::var(key).ok())?;
    let public_key = std::fs::read(&config.gateway_public_key_file)?;
    let verifier = GatewayTokenVerifier::new(&public_key, &config.gateway_id, &config.audience)?;
    let template = SpiffeIdTemplate::new(&config.spiffe_id_template, &config.sandbox_namespace)?;
    let identity = Identity::from_pem(
        std::fs::read(&config.tls_cert_file)?,
        std::fs::read(&config.tls_key_file)?,
    );

    let service = RegistrarService::new(
        verifier,
        config.audience.clone(),
        config.trust_domain.clone(),
        template,
        Arc::new(KeycloakAdmin::new(config.keycloak.clone())),
    );

    info!(
        listen = %config.listen_addr,
        gateway_id = %config.gateway_id,
        audience = %config.audience,
        realm = %config.keycloak.realm,
        "keycloak registrar interceptor starting"
    );
    Server::builder()
        .tls_config(ServerTlsConfig::new().identity(identity))?
        .add_service(GatewayInterceptorServer::new(service))
        .serve_with_shutdown(config.listen_addr, async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
