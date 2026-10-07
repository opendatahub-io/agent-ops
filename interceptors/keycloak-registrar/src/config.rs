// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Environment configuration.

use std::net::SocketAddr;
use std::path::PathBuf;

use crate::keycloak::KeycloakConfig;

pub const DEFAULT_AUDIENCE: &str = "urn:openshell:extension:interceptor:keycloak-registrar";

#[derive(Debug, Clone)]
pub struct Config {
    pub listen_addr: SocketAddr,
    pub tls_cert_file: PathBuf,
    pub tls_key_file: PathBuf,
    /// Gateway Ed25519 public key (PEM) used to verify extension tokens.
    pub gateway_public_key_file: PathBuf,
    pub gateway_id: String,
    /// Must equal the gateway registration's `audience`.
    pub audience: String,
    pub trust_domain: String,
    /// Empty selects the default template.
    pub spiffe_id_template: String,
    /// Namespace of the sandbox supervisor pods (the `{namespace}` placeholder).
    pub sandbox_namespace: String,
    pub keycloak: KeycloakConfig,
}

impl Config {
    pub fn from_env(get: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let required = |key: &str| {
            get(key)
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| format!("{key} is required"))
        };
        let optional = |key: &str, default: &str| get(key).unwrap_or_else(|| default.to_string());
        let listen = optional("LISTEN_ADDR", "0.0.0.0:8443");
        Ok(Self {
            listen_addr: listen
                .parse()
                .map_err(|e| format!("LISTEN_ADDR {listen:?}: {e}"))?,
            tls_cert_file: required("TLS_CERT_FILE")?.into(),
            tls_key_file: required("TLS_KEY_FILE")?.into(),
            gateway_public_key_file: required("GATEWAY_PUBLIC_KEY_FILE")?.into(),
            gateway_id: required("GATEWAY_ID")?,
            audience: optional("INTERCEPTOR_AUDIENCE", DEFAULT_AUDIENCE),
            trust_domain: required("SPIFFE_TRUST_DOMAIN")?,
            spiffe_id_template: optional("SPIFFE_ID_TEMPLATE", ""),
            sandbox_namespace: optional("SANDBOX_NAMESPACE", ""),
            keycloak: KeycloakConfig {
                base_url: required("KEYCLOAK_URL")?,
                realm: required("KEYCLOAK_REALM")?,
                client_id: required("KEYCLOAK_CLIENT_ID")?,
                client_secret: required("KEYCLOAK_CLIENT_SECRET")?,
                identity_provider: optional("KEYCLOAK_IDENTITY_PROVIDER", "spiffe"),
                gateway_client_id: required("GATEWAY_CLIENT_ID")?,
                default_scopes: optional("DEFAULT_CLIENT_SCOPES", "")
                    .split(',')
                    .map(str::trim)
                    .filter(|scope| !scope.is_empty())
                    .map(str::to_string)
                    .collect(),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |key| map.get(key).cloned()
    }

    const REQUIRED: &[(&str, &str)] = &[
        ("TLS_CERT_FILE", "/tls/tls.crt"),
        ("TLS_KEY_FILE", "/tls/tls.key"),
        ("GATEWAY_PUBLIC_KEY_FILE", "/gateway/public.pem"),
        ("GATEWAY_ID", "openshell"),
        ("SPIFFE_TRUST_DOMAIN", "spiffe://openshell.local"),
        ("KEYCLOAK_URL", "http://keycloak"),
        ("KEYCLOAK_REALM", "openshell"),
        ("KEYCLOAK_CLIENT_ID", "registrar"),
        ("KEYCLOAK_CLIENT_SECRET", "secret"),
        (
            "GATEWAY_CLIENT_ID",
            "spiffe://openshell.local/ns/openshell/sa/openshell",
        ),
    ];

    #[test]
    fn applies_defaults() {
        let config = Config::from_env(env(REQUIRED)).unwrap();
        assert_eq!(config.listen_addr.to_string(), "0.0.0.0:8443");
        assert_eq!(
            config.audience,
            "urn:openshell:extension:interceptor:keycloak-registrar"
        );
        assert_eq!(config.keycloak.identity_provider, "spiffe");
        assert!(config.keycloak.default_scopes.is_empty());
        assert_eq!(config.spiffe_id_template, "");
    }

    #[test]
    fn splits_default_scopes() {
        let mut pairs = REQUIRED.to_vec();
        pairs.push(("DEFAULT_CLIENT_SCOPES", "alpha-svc, beta-svc,,"));
        let config = Config::from_env(env(&pairs)).unwrap();
        assert_eq!(config.keycloak.default_scopes, ["alpha-svc", "beta-svc"]);
    }

    #[test]
    fn names_each_missing_required_variable() {
        for (missing, _) in REQUIRED {
            let pairs: Vec<_> = REQUIRED
                .iter()
                .filter(|(k, _)| k != missing)
                .copied()
                .collect();
            let err = Config::from_env(env(&pairs)).unwrap_err();
            assert!(err.contains(missing), "error for {missing}: {err}");
        }
    }

    #[test]
    fn rejects_invalid_listen_address() {
        let mut pairs = REQUIRED.to_vec();
        pairs.push(("LISTEN_ADDR", "not-an-address"));
        assert!(
            Config::from_env(env(&pairs))
                .unwrap_err()
                .contains("LISTEN_ADDR")
        );
    }
}
