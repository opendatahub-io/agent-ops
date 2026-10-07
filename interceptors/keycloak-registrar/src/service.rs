// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! `GatewayInterceptor` implementation: post-commit observer for sandbox
//! creation and deletion.

use std::collections::HashMap;
use std::sync::Arc;

use openshell_core::extension_protocol::{
    ExtensionFamily, extension_metadata, validate_gateway_metadata,
};
use openshell_core::proto::gateway_interceptor::v1::{
    DescribeRequest, GatewayInterceptorPhase, InterceptorBinding, InterceptorEvaluation,
    InterceptorManifest, InterceptorResult, InterceptorSelector, ProviderProfileSnapshot,
    ProviderProfileSnapshotRequest, gateway_interceptor_server::GatewayInterceptor,
    interceptor_evaluation,
};
use prost_types::{Struct, Value as ProtoValue, value::Kind};
use serde_json::Value;
use tonic::{Request, Response, Status};
use tracing::{info, warn};

use crate::auth::GatewayTokenVerifier;
use crate::keycloak::KeycloakAdmin;
use crate::spiffe::SpiffeIdTemplate;

const SERVICE: &str = "openshell.v1.OpenShell";
const NAME: &str = "keycloak-registrar";
const SPIFFE_ID_ANNOTATION: &str = "keycloak_registrar.spiffe_id";
const OUTCOME_ANNOTATION: &str = "keycloak_registrar.outcome";

/// Registers and removes the Keycloak objects for one sandbox SPIFFE ID.
#[tonic::async_trait]
pub trait SandboxRegistrar: Send + Sync + 'static {
    async fn ensure_sandbox(&self, spiffe_id: &str) -> Result<(), String>;
    async fn delete_sandbox(&self, spiffe_id: &str) -> Result<(), String>;
}

#[tonic::async_trait]
impl SandboxRegistrar for KeycloakAdmin {
    async fn ensure_sandbox(&self, spiffe_id: &str) -> Result<(), String> {
        KeycloakAdmin::ensure_sandbox(self, spiffe_id).await
    }
    async fn delete_sandbox(&self, spiffe_id: &str) -> Result<(), String> {
        KeycloakAdmin::delete_sandbox(self, spiffe_id).await
    }
}

pub struct RegistrarService {
    verifier: GatewayTokenVerifier,
    audience: String,
    trust_domain: String,
    template: SpiffeIdTemplate,
    registrar: Arc<dyn SandboxRegistrar>,
}

impl RegistrarService {
    pub fn new(
        verifier: GatewayTokenVerifier,
        audience: String,
        trust_domain: String,
        template: SpiffeIdTemplate,
        registrar: Arc<dyn SandboxRegistrar>,
    ) -> Self {
        Self {
            verifier,
            audience,
            trust_domain,
            template,
            registrar,
        }
    }

    fn authenticate<T>(&self, request: &Request<T>) -> Result<(), Status> {
        let authorization = request
            .metadata()
            .get("authorization")
            .and_then(|value| value.to_str().ok());
        self.verifier
            .verify(authorization)
            .map(|_| ())
            .map_err(|reason| {
                warn!(%reason, "rejected interceptor call");
                Status::unauthenticated("gateway authentication failed")
            })
    }

    fn manifest(&self) -> InterceptorManifest {
        let binding = |id: &str, method: &str| InterceptorBinding {
            id: id.to_string(),
            selector: Some(InterceptorSelector {
                rpc: format!("{SERVICE}/{method}"),
                service: String::new(),
                method: String::new(),
            }),
            phases: vec![GatewayInterceptorPhase::PostCommit as i32],
            // post_commit cannot revoke a committed operation, so the gateway
            // requires fail_open.
            failure_policy: "fail_open".to_string(),
        };
        InterceptorManifest {
            name: NAME.to_string(),
            bindings: vec![
                binding("register-on-create", "CreateSandbox"),
                binding("deregister-on-delete", "DeleteSandbox"),
            ],
            failure_policy: "fail_open".to_string(),
            provider_profiles: false,
            expected_audience: self.audience.clone(),
            extension: Some(extension_metadata(
                ExtensionFamily::GatewayInterceptor,
                format!("openshell/{NAME}"),
                env!("CARGO_PKG_VERSION"),
                [],
            )),
        }
    }

    async fn observe(&self, evaluation: &InterceptorEvaluation) -> HashMap<String, String> {
        let mut annotations = HashMap::new();
        let Some(interceptor_evaluation::Phase::PostCommit(post_commit)) = &evaluation.phase else {
            annotations.insert(
                OUTCOME_ANNOTATION.to_string(),
                "skipped: not post_commit".to_string(),
            );
            return annotations;
        };
        let response = post_commit
            .committed_response
            .as_ref()
            .map(struct_to_json)
            .unwrap_or(Value::Null);
        let (sandbox_id, creating) = match evaluation.method.as_str() {
            "CreateSandbox" => (response.pointer("/sandbox/metadata/id"), true),
            "DeleteSandbox" => (response.pointer("/sandboxId"), false),
            other => {
                annotations.insert(OUTCOME_ANNOTATION.to_string(), format!("skipped: {other}"));
                return annotations;
            }
        };
        let Some(sandbox_id) = sandbox_id
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
        else {
            annotations.insert(
                OUTCOME_ANNOTATION.to_string(),
                "skipped: no sandbox id".to_string(),
            );
            return annotations;
        };

        let spiffe_id = self.template.render(&self.trust_domain, sandbox_id);
        let result = if creating {
            self.registrar
                .ensure_sandbox(&spiffe_id)
                .await
                .map(|()| "registered")
        } else {
            self.registrar
                .delete_sandbox(&spiffe_id)
                .await
                .map(|()| "deregistered")
        };
        let outcome = match result {
            Ok(outcome) => {
                info!(%sandbox_id, %spiffe_id, outcome, "keycloak registration updated");
                outcome.to_string()
            }
            Err(error) => {
                warn!(%sandbox_id, %spiffe_id, %error, "keycloak registration failed");
                format!("error: {error}")
            }
        };
        annotations.insert(SPIFFE_ID_ANNOTATION.to_string(), spiffe_id);
        annotations.insert(OUTCOME_ANNOTATION.to_string(), outcome);
        annotations
    }
}

#[tonic::async_trait]
impl GatewayInterceptor for RegistrarService {
    async fn describe(
        &self,
        request: Request<DescribeRequest>,
    ) -> Result<Response<InterceptorManifest>, Status> {
        self.authenticate(&request)?;
        let manifest = self.manifest();
        validate_gateway_metadata(
            ExtensionFamily::GatewayInterceptor,
            NAME,
            manifest.extension.as_ref(),
            request.into_inner().gateway,
        )
        .map_err(|error| Status::failed_precondition(error.to_string()))?;
        Ok(Response::new(manifest))
    }

    async fn snapshot_provider_profiles(
        &self,
        _request: Request<ProviderProfileSnapshotRequest>,
    ) -> Result<Response<ProviderProfileSnapshot>, Status> {
        Err(Status::unimplemented(
            "this interceptor does not vend provider profiles",
        ))
    }

    async fn evaluate(
        &self,
        request: Request<InterceptorEvaluation>,
    ) -> Result<Response<InterceptorResult>, Status> {
        self.authenticate(&request)?;
        let log_annotations = self.observe(request.get_ref()).await;
        Ok(Response::new(InterceptorResult {
            allowed: true,
            reason: String::new(),
            status_code: String::new(),
            patches: Vec::new(),
            log_annotations,
        }))
    }
}

pub(crate) fn struct_to_json(value: &Struct) -> Value {
    Value::Object(
        value
            .fields
            .iter()
            .map(|(k, v)| (k.clone(), proto_value_to_json(v)))
            .collect(),
    )
}

fn proto_value_to_json(value: &ProtoValue) -> Value {
    match &value.kind {
        Some(Kind::StringValue(s)) => Value::String(s.clone()),
        Some(Kind::NumberValue(n)) => {
            serde_json::Number::from_f64(*n).map_or(Value::Null, Value::Number)
        }
        Some(Kind::BoolValue(b)) => Value::Bool(*b),
        Some(Kind::StructValue(s)) => struct_to_json(s),
        Some(Kind::ListValue(list)) => {
            Value::Array(list.values.iter().map(proto_value_to_json).collect())
        }
        Some(Kind::NullValue(_)) | None => Value::Null,
    }
}

#[cfg(test)]
pub(crate) fn json_to_struct(value: &Value) -> Struct {
    fn to_proto(value: &Value) -> ProtoValue {
        let kind = match value {
            Value::Null => Kind::NullValue(0),
            Value::Bool(b) => Kind::BoolValue(*b),
            Value::Number(n) => Kind::NumberValue(n.as_f64().unwrap_or_default()),
            Value::String(s) => Kind::StringValue(s.clone()),
            Value::Array(items) => Kind::ListValue(prost_types::ListValue {
                values: items.iter().map(to_proto).collect(),
            }),
            Value::Object(_) => Kind::StructValue(json_to_struct(value)),
        };
        ProtoValue { kind: Some(kind) }
    }
    Struct {
        fields: value
            .as_object()
            .map(|map| map.iter().map(|(k, v)| (k.clone(), to_proto(v))).collect())
            .unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;
    use crate::auth::test_keys::{self, Keys};
    use openshell_core::proto::gateway_interceptor::v1::{
        DescribeRequest, GatewayInterceptorPhase, InterceptorEvaluation, PostCommitEvaluation,
        gateway_interceptor_server::GatewayInterceptor, interceptor_evaluation,
    };
    use serde_json::json;
    use tonic::{Code, Request};

    const GW: &str = "openshell";
    const AUD: &str = "urn:openshell:extension:interceptor:keycloak-registrar";

    #[derive(Default)]
    struct FakeRegistrar {
        ensured: Mutex<Vec<String>>,
        deleted: Mutex<Vec<String>>,
        fail: bool,
    }

    #[tonic::async_trait]
    impl SandboxRegistrar for FakeRegistrar {
        async fn ensure_sandbox(&self, spiffe_id: &str) -> Result<(), String> {
            self.ensured.lock().unwrap().push(spiffe_id.to_string());
            if self.fail {
                Err("keycloak unavailable".to_string())
            } else {
                Ok(())
            }
        }
        async fn delete_sandbox(&self, spiffe_id: &str) -> Result<(), String> {
            self.deleted.lock().unwrap().push(spiffe_id.to_string());
            Ok(())
        }
    }

    fn service(keys: &Keys, registrar: Arc<FakeRegistrar>) -> RegistrarService {
        RegistrarService::new(
            GatewayTokenVerifier::new(keys.public_pem.as_bytes(), GW, AUD).unwrap(),
            AUD.to_string(),
            "spiffe://openshell.local".to_string(),
            SpiffeIdTemplate::new("", "openshell").unwrap(),
            registrar,
        )
    }

    fn authed<T>(keys: &Keys, message: T) -> Request<T> {
        let token = test_keys::sign_gateway(keys, &test_keys::gateway_claims(GW, AUD));
        let mut request = Request::new(message);
        request
            .metadata_mut()
            .insert("authorization", format!("Bearer {token}").parse().unwrap());
        request
    }

    fn post_commit(method: &str, response: serde_json::Value) -> InterceptorEvaluation {
        InterceptorEvaluation {
            interceptor_name: "keycloak-registrar".to_string(),
            binding_id: "b".to_string(),
            service: "openshell.v1.OpenShell".to_string(),
            method: method.to_string(),
            principal: Default::default(),
            phase: Some(interceptor_evaluation::Phase::PostCommit(
                PostCommitEvaluation {
                    committed_response: Some(json_to_struct(&response)),
                },
            )),
        }
    }

    fn gateway_metadata() -> DescribeRequest {
        DescribeRequest {
            gateway: Some(openshell_core::extension_protocol::gateway_metadata(
                openshell_core::extension_protocol::ExtensionFamily::GatewayInterceptor,
            )),
        }
    }

    #[tokio::test]
    async fn describe_declares_post_commit_fail_open_bindings() {
        let keys = test_keys::generate();
        let svc = service(&keys, Arc::default());
        let manifest = svc
            .describe(authed(&keys, gateway_metadata()))
            .await
            .unwrap()
            .into_inner();

        assert_eq!(manifest.expected_audience, AUD);
        let rpcs: Vec<_> = manifest
            .bindings
            .iter()
            .map(|b| b.selector.as_ref().unwrap().rpc.clone())
            .collect();
        assert_eq!(
            rpcs,
            [
                "openshell.v1.OpenShell/CreateSandbox",
                "openshell.v1.OpenShell/DeleteSandbox"
            ]
        );
        for binding in &manifest.bindings {
            assert_eq!(
                binding.phases,
                vec![GatewayInterceptorPhase::PostCommit as i32]
            );
            assert_eq!(binding.failure_policy, "fail_open");
        }
    }

    #[tokio::test]
    async fn describe_rejects_unsupported_gateway_protocol() {
        let keys = test_keys::generate();
        let svc = service(&keys, Arc::default());
        let mut request = gateway_metadata();
        request
            .gateway
            .as_mut()
            .unwrap()
            .protocol_version
            .as_mut()
            .unwrap()
            .major = 2;
        let err = svc.describe(authed(&keys, request)).await.unwrap_err();
        assert_eq!(err.code(), Code::FailedPrecondition);
    }

    #[tokio::test]
    async fn describe_requires_gateway_token() {
        let keys = test_keys::generate();
        let svc = service(&keys, Arc::default());
        let err = svc
            .describe(Request::new(gateway_metadata()))
            .await
            .unwrap_err();
        assert_eq!(err.code(), Code::Unauthenticated);
    }

    #[tokio::test]
    async fn create_sandbox_registers_spiffe_id() {
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar::default());
        let svc = service(&keys, registrar.clone());
        let response = json!({"sandbox": {"metadata": {"id": "9c4daea2", "name": "agent-a"}}});

        let result = svc
            .evaluate(authed(&keys, post_commit("CreateSandbox", response)))
            .await
            .unwrap()
            .into_inner();

        let want =
            "spiffe://openshell.local/openshell/sandbox/openshell.os-supervisor-9c4daea2.9c4daea2";
        assert!(result.allowed);
        assert_eq!(*registrar.ensured.lock().unwrap(), vec![want.to_string()]);
        assert_eq!(result.log_annotations["keycloak_registrar.spiffe_id"], want);
        assert_eq!(
            result.log_annotations["keycloak_registrar.outcome"],
            "registered"
        );
    }

    #[tokio::test]
    async fn delete_sandbox_deregisters_spiffe_id() {
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar::default());
        let svc = service(&keys, registrar.clone());
        let response = json!({"outcome": "DELETION_OUTCOME_ACCEPTED", "sandboxId": "3c1cfe14"});

        let result = svc
            .evaluate(authed(&keys, post_commit("DeleteSandbox", response)))
            .await
            .unwrap()
            .into_inner();

        assert!(result.allowed);
        assert_eq!(
            *registrar.deleted.lock().unwrap(),
            vec!["spiffe://openshell.local/openshell/sandbox/openshell.os-supervisor-3c1cfe14.3c1cfe14".to_string()]
        );
        assert_eq!(
            result.log_annotations["keycloak_registrar.outcome"],
            "deregistered"
        );
    }

    #[tokio::test]
    async fn registrar_failure_is_logged_not_denied() {
        // post_commit cannot deny: the sandbox already exists.
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar {
            fail: true,
            ..Default::default()
        });
        let svc = service(&keys, registrar);
        let response = json!({"sandbox": {"metadata": {"id": "9c4daea2"}}});

        let result = svc
            .evaluate(authed(&keys, post_commit("CreateSandbox", response)))
            .await
            .unwrap()
            .into_inner();

        assert!(result.allowed);
        assert!(
            result.log_annotations["keycloak_registrar.outcome"].contains("keycloak unavailable")
        );
    }

    #[tokio::test]
    async fn response_without_sandbox_id_is_skipped() {
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar::default());
        let svc = service(&keys, registrar.clone());

        let result = svc
            .evaluate(authed(
                &keys,
                post_commit("CreateSandbox", json!({"sandbox": {}})),
            ))
            .await
            .unwrap()
            .into_inner();

        assert!(result.allowed);
        assert!(registrar.ensured.lock().unwrap().is_empty());
        assert!(result.log_annotations["keycloak_registrar.outcome"].starts_with("skipped"));
    }

    #[tokio::test]
    async fn evaluate_rejects_unauthenticated_caller_without_side_effects() {
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar::default());
        let svc = service(&keys, registrar.clone());
        let response = json!({"sandbox": {"metadata": {"id": "9c4daea2"}}});

        let err = svc
            .evaluate(Request::new(post_commit("CreateSandbox", response)))
            .await
            .unwrap_err();

        assert_eq!(err.code(), Code::Unauthenticated);
        assert!(registrar.ensured.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn non_post_commit_phase_is_allowed_without_side_effects() {
        let keys = test_keys::generate();
        let registrar = Arc::new(FakeRegistrar::default());
        let svc = service(&keys, registrar.clone());
        let mut evaluation = post_commit("CreateSandbox", json!({}));
        evaluation.phase = Some(interceptor_evaluation::Phase::Validate(Default::default()));

        let result = svc
            .evaluate(authed(&keys, evaluation))
            .await
            .unwrap()
            .into_inner();

        assert!(result.allowed);
        assert!(registrar.ensured.lock().unwrap().is_empty());
    }
}
