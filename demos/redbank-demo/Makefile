SHELL := bash

NAMESPACE ?= redbank-demo
export NAMESPACE

LOAD_ENV = [ -f .env ] || { echo "ERROR: .env not found — run 'make init' first"; exit 1; } && set -a && source .env && set +a

.PHONY: init deploy deploy-db deploy-mcp deploy-banking deploy-knowledge deploy-orchestrator deploy-playground clean setup-keycloak test-pgvector compile-pipeline test-knowledge-agent help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-24s %s\n", $$1, $$2}'

init: ## Create .env from .env.example
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example — edit it with your configuration"; else echo ".env already exists — skipping"; fi

deploy: setup-keycloak deploy-db deploy-mcp deploy-banking deploy-knowledge deploy-orchestrator deploy-playground ## Deploy all RedBank components
	@echo "" && echo "RedBank Kagenti demo deployed to namespace $${NAMESPACE:-redbank-demo}"

deploy-db: ## Deploy PostgreSQL database
	@$(LOAD_ENV) && \
	  oc new-project $${NAMESPACE} 2>/dev/null || oc project $${NAMESPACE} && \
	  cd postgres-db && oc apply -k .

deploy-mcp: ## Build and deploy MCP server
	@$(LOAD_ENV) && cd mcp-server && bash deploy.sh

deploy-banking: ## Build and deploy banking agent
	@$(LOAD_ENV) && cd banking-agent && bash deploy.sh

deploy-knowledge: ## Build and deploy knowledge agent
	@$(LOAD_ENV) && cd knowledge-agent && bash deploy.sh

deploy-orchestrator: ## Build and deploy orchestrator agent
	@$(LOAD_ENV) && cd orchestrator-agent && bash deploy.sh

deploy-playground: ## Build and deploy playground UI
	@$(LOAD_ENV) && cd playground && bash deploy.sh

clean: ## Remove all RedBank workloads (keeps namespace and build configs)
	@$(LOAD_ENV) 2>/dev/null; bash scripts/cleanup.sh

setup-keycloak: ## Configure Keycloak realm and clients
	@$(LOAD_ENV) && bash scripts/setup-keycloak.sh

test-pgvector: ## Run PGVector tests
	cd langchain-pgvector && python3 -m pytest tests/ -v

compile-pipeline: ## Compile the RAG pipeline
	cd langchain-pgvector/pipeline && python3 pgvector_rag_pipeline.py

test-knowledge-agent: ## Test knowledge agent (port-forwards and runs tests)
	@echo "Port-forwarding knowledge agent (background)..."
	@oc port-forward svc/redbank-knowledge-agent 8002:8002 &
	@sleep 2
	@bash scripts/test-knowledge-agent.sh; RC=$$?; kill %1 2>/dev/null; exit $$RC
