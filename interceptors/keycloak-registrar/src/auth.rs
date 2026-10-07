// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Verification of the short-lived extension token the gateway sends on every
//! interceptor call (see docs/extensibility/overview.mdx, "Authenticating Extensions").

use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use openshell_extension_core::{EXTENSION_JWT_TYP, ExtensionCallerKind, ExtensionJwtClaims};

/// Verifies gateway-minted extension tokens against a pinned public key.
#[derive(Clone)]
pub struct GatewayTokenVerifier {
    key: DecodingKey,
    validation: Validation,
}

impl GatewayTokenVerifier {
    /// `public_key_pem` is the gateway's Ed25519 public key, `gateway_id` its
    /// configured ID, and `audience` the interceptor registration's audience.
    pub fn new(public_key_pem: &[u8], gateway_id: &str, audience: &str) -> Result<Self, String> {
        let key = DecodingKey::from_ed_pem(public_key_pem)
            .map_err(|e| format!("invalid gateway public key: {e}"))?;
        // Pin the algorithm instead of trusting the token header.
        let mut validation = Validation::new(Algorithm::EdDSA);
        validation.set_issuer(&[format!("openshell-gateway:{gateway_id}")]);
        validation.set_audience(&[audience]);
        validation.set_required_spec_claims(&["iss", "aud", "sub", "iat", "exp"]);
        Ok(Self { key, validation })
    }

    /// Verifies the `authorization` metadata value of one interceptor call.
    pub fn verify(&self, authorization: Option<&str>) -> Result<ExtensionJwtClaims, String> {
        let token = authorization
            .and_then(|value| value.strip_prefix("Bearer "))
            .ok_or_else(|| "missing bearer token".to_string())?;
        let header = decode_header(token).map_err(|e| format!("malformed token: {e}"))?;
        if header.typ.as_deref() != Some(EXTENSION_JWT_TYP) {
            return Err(format!(
                "token typ {:?} is not {EXTENSION_JWT_TYP}",
                header.typ
            ));
        }
        let claims = decode::<ExtensionJwtClaims>(token, &self.key, &self.validation)
            .map_err(|e| format!("invalid token: {e}"))?
            .claims;
        if claims.caller_kind != ExtensionCallerKind::Gateway || claims.sandbox_id.is_some() {
            return Err(format!(
                "caller_kind {:?} is not accepted; only the gateway may call this interceptor",
                claims.caller_kind
            ));
        }
        Ok(claims)
    }
}

#[cfg(test)]
pub(crate) mod test_keys {
    use ed25519_dalek::SigningKey;
    use ed25519_dalek::pkcs8::{EncodePrivateKey, EncodePublicKey, spki::der::pem::LineEnding};
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    use openshell_extension_core::{EXTENSION_JWT_TYP, ExtensionCallerKind, ExtensionJwtClaims};

    pub struct Keys {
        pub private_pem: String,
        pub public_pem: String,
    }

    pub fn generate() -> Keys {
        let key = SigningKey::generate(&mut rand::rngs::OsRng);
        Keys {
            private_pem: key.to_pkcs8_pem(LineEnding::LF).unwrap().to_string(),
            public_pem: key
                .verifying_key()
                .to_public_key_pem(LineEnding::LF)
                .unwrap(),
        }
    }

    pub fn now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64
    }

    pub fn gateway_claims(gateway_id: &str, audience: &str) -> ExtensionJwtClaims {
        ExtensionJwtClaims {
            iss: format!("openshell-gateway:{gateway_id}"),
            aud: audience.to_string(),
            sub: format!("openshell-gateway:{gateway_id}"),
            iat: now(),
            exp: now() + 300,
            jti: "jti-1".to_string(),
            caller_kind: ExtensionCallerKind::Gateway,
            sandbox_id: None,
        }
    }

    pub fn sign(keys: &Keys, claims: &ExtensionJwtClaims, typ: Option<&str>) -> String {
        let mut header = Header::new(Algorithm::EdDSA);
        header.kid = Some("kid-1".to_string());
        header.typ = typ.map(str::to_string);
        encode(
            &header,
            claims,
            &EncodingKey::from_ed_pem(keys.private_pem.as_bytes()).unwrap(),
        )
        .unwrap()
    }

    pub fn sign_gateway(keys: &Keys, claims: &ExtensionJwtClaims) -> String {
        sign(keys, claims, Some(EXTENSION_JWT_TYP))
    }
}

#[cfg(test)]
mod tests {
    use super::test_keys::*;
    use super::*;
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    use openshell_extension_core::ExtensionCallerKind;

    const GW: &str = "openshell";
    const AUD: &str = "urn:openshell:extension:interceptor:keycloak-registrar";

    fn verifier(keys: &Keys) -> GatewayTokenVerifier {
        GatewayTokenVerifier::new(keys.public_pem.as_bytes(), GW, AUD).unwrap()
    }

    fn bearer(token: &str) -> String {
        format!("Bearer {token}")
    }

    #[test]
    fn accepts_valid_gateway_token() {
        let keys = generate();
        let token = sign_gateway(&keys, &gateway_claims(GW, AUD));
        let claims = verifier(&keys).verify(Some(&bearer(&token))).unwrap();
        assert_eq!(claims.caller_kind, ExtensionCallerKind::Gateway);
    }

    #[test]
    fn rejects_missing_or_non_bearer_header() {
        let keys = generate();
        let token = sign_gateway(&keys, &gateway_claims(GW, AUD));
        assert!(verifier(&keys).verify(None).is_err());
        assert!(verifier(&keys).verify(Some(&token)).is_err());
    }

    #[test]
    fn rejects_token_without_extension_typ() {
        // A sandbox bootstrap token is signed by the same key; only typ and aud differ.
        let keys = generate();
        let token = sign(&keys, &gateway_claims(GW, AUD), Some("JWT"));
        let err = verifier(&keys).verify(Some(&bearer(&token))).unwrap_err();
        assert!(err.contains("typ"), "{err}");
    }

    #[test]
    fn rejects_non_eddsa_algorithm() {
        let keys = generate();
        let mut header = Header::new(Algorithm::HS256);
        header.typ = Some(openshell_extension_core::EXTENSION_JWT_TYP.to_string());
        let token = encode(
            &header,
            &gateway_claims(GW, AUD),
            &EncodingKey::from_secret(keys.public_pem.as_bytes()),
        )
        .unwrap();
        assert!(verifier(&keys).verify(Some(&bearer(&token))).is_err());
    }

    #[test]
    fn rejects_wrong_audience() {
        let keys = generate();
        let token = sign_gateway(
            &keys,
            &gateway_claims(GW, "urn:openshell:extension:interceptor:other"),
        );
        assert!(verifier(&keys).verify(Some(&bearer(&token))).is_err());
    }

    #[test]
    fn rejects_wrong_issuer() {
        let keys = generate();
        let mut claims = gateway_claims(GW, AUD);
        claims.iss = "openshell-gateway:someone-else".to_string();
        assert!(
            verifier(&keys)
                .verify(Some(&bearer(&sign_gateway(&keys, &claims))))
                .is_err()
        );
    }

    #[test]
    fn rejects_supervisor_caller() {
        let keys = generate();
        let mut claims = gateway_claims(GW, AUD);
        claims.caller_kind = ExtensionCallerKind::Supervisor;
        claims.sandbox_id = Some("sandbox-1".to_string());
        let err = verifier(&keys)
            .verify(Some(&bearer(&sign_gateway(&keys, &claims))))
            .unwrap_err();
        assert!(err.contains("caller_kind"), "{err}");
    }

    #[test]
    fn rejects_expired_token() {
        let keys = generate();
        let mut claims = gateway_claims(GW, AUD);
        claims.iat = now() - 7200;
        claims.exp = now() - 3600;
        assert!(
            verifier(&keys)
                .verify(Some(&bearer(&sign_gateway(&keys, &claims))))
                .is_err()
        );
    }

    #[test]
    fn rejects_token_signed_by_another_key() {
        let keys = generate();
        let attacker = generate();
        let token = sign_gateway(&attacker, &gateway_claims(GW, AUD));
        assert!(verifier(&keys).verify(Some(&bearer(&token))).is_err());
    }

    #[test]
    fn rejects_invalid_public_key() {
        assert!(GatewayTokenVerifier::new(b"not a pem", GW, AUD).is_err());
    }
}
