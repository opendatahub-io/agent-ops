// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Sandbox supervisor SPIFFE ID template.

use std::sync::LazyLock;

use regex::Regex;

/// Matches the per-sandbox ClusterSPIFFEID (`<namespace>.<pod-name>.<sandbox-id>`,
/// proposed by Gordon Sim in upstream NVIDIA/OpenShell#3100). Keycloak matches the SVID subject
/// exactly, so this must equal the template SPIRE uses to issue supervisor SVIDs.
/// The pod-qualified form binds the ID to the one supervisor pod OpenShell
/// creates, so a pod that only copies the sandbox-id annotation gets a
/// different ID with no Keycloak client.
pub const DEFAULT_TEMPLATE: &str =
    "{trustDomain}/openshell/sandbox/{namespace}.{podName}.{sandboxID}";

/// Only values available for both CreateSandbox and DeleteSandbox are allowed,
/// so every registered ID can also be deregistered: the sandbox ID comes from
/// the response, the namespace from configuration, and the pod name is derived
/// from the sandbox ID.
const KNOWN_PLACEHOLDERS: &[&str] = &["{trustDomain}", "{namespace}", "{podName}", "{sandboxID}"];

/// The Kubernetes driver names the supervisor pod `os-supervisor-<lowercased sandbox ID>`.
const SUPERVISOR_POD_PREFIX: &str = "os-supervisor-";

static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{[^{}]*\}").unwrap());

#[derive(Debug, Clone)]
pub struct SpiffeIdTemplate {
    template: String,
    namespace: String,
}

impl SpiffeIdTemplate {
    /// Validates a template. An empty template selects [`DEFAULT_TEMPLATE`].
    /// `namespace` is the namespace the sandbox supervisor pods run in.
    pub fn new(template: &str, namespace: &str) -> Result<Self, String> {
        let template = if template.is_empty() {
            DEFAULT_TEMPLATE
        } else {
            template
        };
        for placeholder in PLACEHOLDER.find_iter(template) {
            if !KNOWN_PLACEHOLDERS.contains(&placeholder.as_str()) {
                return Err(format!(
                    "SPIFFE ID template {template:?}: unknown placeholder {}",
                    placeholder.as_str()
                ));
            }
        }
        if !template.contains("{sandboxID}") {
            return Err(format!(
                "SPIFFE ID template {template:?} must contain {{sandboxID}}"
            ));
        }
        if template.contains("{namespace}") && namespace.trim().is_empty() {
            return Err(format!(
                "SPIFFE ID template {template:?} uses {{namespace}}; set SANDBOX_NAMESPACE"
            ));
        }
        Ok(Self {
            template: template.to_string(),
            namespace: namespace.trim().to_string(),
        })
    }

    pub fn render(&self, trust_domain: &str, sandbox_id: &str) -> String {
        let pod_name = format!("{SUPERVISOR_POD_PREFIX}{}", sandbox_id.to_ascii_lowercase());
        self.template
            .replace("{trustDomain}", trust_domain.trim_end_matches('/'))
            .replace("{namespace}", &self.namespace)
            .replace("{podName}", &pod_name)
            .replace("{sandboxID}", sandbox_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_template_qualifies_the_supervisor_pod() {
        // Matches upstream NVIDIA/OpenShell#3100: <namespace>.<pod-name>.<sandbox-id>.
        let template = SpiffeIdTemplate::new("", "openshell").unwrap();
        assert_eq!(
            template.render("spiffe://openshell.local", "9c4daea2"),
            "spiffe://openshell.local/openshell/sandbox/openshell.os-supervisor-9c4daea2.9c4daea2"
        );
    }

    #[test]
    fn pod_name_is_lowercased_like_the_kubernetes_driver() {
        let template = SpiffeIdTemplate::new("{trustDomain}/{podName}/{sandboxID}", "ns").unwrap();
        assert_eq!(
            template.render("spiffe://td", "AB12"),
            "spiffe://td/os-supervisor-ab12/AB12"
        );
    }

    #[test]
    fn renders_custom_template_and_trims_trailing_slash() {
        let template = SpiffeIdTemplate::new("{trustDomain}/workloads/{sandboxID}", "").unwrap();
        assert_eq!(
            template.render("spiffe://td/", "id-1"),
            "spiffe://td/workloads/id-1"
        );
    }

    #[test]
    fn namespace_placeholder_requires_a_namespace() {
        let err = SpiffeIdTemplate::new("", "").unwrap_err();
        assert!(err.contains("SANDBOX_NAMESPACE"), "{err}");
    }

    #[test]
    fn rejects_name_placeholder() {
        // DeleteSandbox only returns the sandbox ID, so a name-based ID could
        // be registered but never deregistered.
        let err = SpiffeIdTemplate::new("{trustDomain}/{name}/{sandboxID}", "ns").unwrap_err();
        assert!(err.contains("{name}"), "{err}");
    }

    #[test]
    fn rejects_template_without_sandbox_id() {
        let err = SpiffeIdTemplate::new("{trustDomain}/openshell/static", "ns").unwrap_err();
        assert!(err.contains("{sandboxID}"), "{err}");
    }

    #[test]
    fn rejects_unknown_placeholder() {
        let err = SpiffeIdTemplate::new("{trustDomain}/{workspace}/{sandboxID}", "ns").unwrap_err();
        assert!(err.contains("{workspace}"), "{err}");
    }
}
