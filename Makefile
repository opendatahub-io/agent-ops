# OpenShell on OpenShift: one-command entry points. Every target is safe to re-run.
# Set OPENSHELL_NAMESPACE to install somewhere other than "openshell".

TE := scripts/token-exchange

.PHONY: help preflight cli deploy token-exchange token-exchange-preflight try-it clean-demo

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-26s %s\n", $$1, $$2}'

preflight: ## Check the cluster is ready for OpenShell
	@./scripts/preflight.sh

cli: ## Install the OpenShell CLI (checksum-verified installer)
	@./scripts/install-openshell-cli.sh

deploy: preflight ## Install OpenShell, expose it, and connect the CLI
	@./scripts/deploy-openshell.sh

token-exchange-preflight:
	@./scripts/preflight.sh --token-exchange

token-exchange: token-exchange-preflight ## Set up per-sandbox identity and token exchange (steps 0 to 5)
	@OPENSHELL_ENABLE_SPIFFE=true ./scripts/deploy-openshell.sh
	@$(TE)/01-fix-ztwim-oidc.sh
	@$(TE)/02-sandbox-spiffe-ids.sh
	@$(TE)/03-deploy-keycloak.sh
	@$(TE)/04-configure-realm.sh
	@$(TE)/05-deploy-registrar.sh

try-it: ## Call a protected API from a sandbox as the demo user (step 6)
	@$(TE)/06-try-it.sh

clean-demo: ## Delete the try-it sandbox
	@openshell sandbox delete token-exchange-demo

dashboard: ## Deploy the OpenShell dashboard (no Route; open with oc port-forward)
	@ns=$${OPENSHELL_NAMESPACE:-openshell}; \
	sed "s/__NAMESPACE__/$$ns/g" common/openshell-dashboard.yaml | oc -n "$$ns" apply -f - && \
	oc -n "$$ns" rollout status deploy/openshell-dashboard --timeout=180s && \
	echo "Open it with: oc -n $$ns port-forward svc/openshell-dashboard 8080:8080, then http://localhost:8080"

switch-images: ## Re-deploy with other images (ODH_IMAGE_TAG, OPENSHELL_HELM_VERSION, ODH_IMAGE_REGISTRY, ODH_IMAGE_REPO_PREFIX) and re-register the interceptor
	@OPENSHELL_ENABLE_SPIFFE=$${OPENSHELL_ENABLE_SPIFFE:-true} ./scripts/deploy-openshell.sh
	@if oc -n "$${OPENSHELL_NAMESPACE:-openshell}" get deploy/keycloak-registrar >/dev/null 2>&1; then $(TE)/05-deploy-registrar.sh; fi

UA := scripts/user-auth

user-auth: ## Turn on user login: Keycloak over HTTPS, OIDC on the gateway, dashboard behind oauth2-proxy
	@$(UA)/01-keycloak-route.sh
	@$(UA)/02-configure-realm.sh
	@$(UA)/03-gateway-oidc.sh
	@$(UA)/04-dashboard-login.sh
	@$(UA)/05-sync-members.sh
	@$(UA)/verify.sh

verify-user-auth: ## Check user login end to end (gateway, roles, workspace membership, dashboard browser login)
	@$(UA)/verify.sh

grant: ## Give a user a workspace: make grant MEMBER=alice WS=team-a [ROLE=user|admin]
	@$(UA)/grant.sh "$(MEMBER)" "$(WS)" "$(or $(ROLE),user)"

revoke: ## Take a workspace away: make revoke MEMBER=alice [WS=team-a] (no WS: every workspace)
	@$(UA)/revoke.sh "$(MEMBER)" $(WS)

sync-members: ## Make workspace membership match Keycloak groups (DRY_RUN=true to preview)
	@$(UA)/05-sync-members.sh

connect-info: ## Print the dashboard link and the one CLI command to send a new user
	@$(UA)/connect-info.sh

byo-agent: ## Build examples/byo-agent on the cluster and run it in a sandbox (see install/04-agent-images.md)
	@./scripts/byo-agent.sh
