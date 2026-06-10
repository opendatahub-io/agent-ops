# Agent Ops

Demos, reference architectures, and tooling for deploying AI agents on OpenShift with [Kagenti](https://github.com/kagenti/kagenti).

Part of Red Hat's **Agents & AI Engineering Tools** organization, the Agent Ops team builds the infrastructure for agent deployment, identity, and token exchange on OpenShift AI.

## Repository Structure

```
agent-ops/
├── demos/
│   └── redbank-demo/       Multi-agent banking demo (MCP + A2A + RLS)
└── README.md
```

## Demos

### [RedBank Demo](demos/redbank-demo/)

A multi-agent banking application that demonstrates the Kagenti platform's core capabilities: agent enrollment, JWT-based identity propagation, AuthBridge sidecar injection, and A2A agent discovery.

**What it shows:**

- **MCP server** with auth-aware tools (FastMCP + PostgreSQL + Row-Level Security)
- **A2A agents** — Orchestrator, Knowledge (RAG), and Banking Operations agents built with LangGraph
- **Kagenti integration** — AgentRuntime CRs, AuthBridge sidecar, AgentCard-based peer discovery
- **RAG pipeline** — LangChain + PGVector with role-scoped document collections (KFP pipeline)
- **Playground UI** — Chat interface with Keycloak OIDC authentication
- **Role-based access control** at three tiers: AuthBridge (network), application (`@admin_only`), and database (RLS)

**Stack:** Python, LangGraph, FastMCP, PostgreSQL 16, PGVector, Keycloak, Helm, OpenShift

See the [RedBank Demo README](demos/redbank-demo/README.md) for architecture details and deployment instructions.

## Related Projects

| Project | Description |
|---------|-------------|
| [kagenti](https://github.com/kagenti/kagenti) | Kagenti platform — Helm charts, AuthBridge, SPIFFE IdP setup |
| [kagenti-operator](https://github.com/kagenti/kagenti-operator) | Kubernetes operator for AgentRuntime and AgentCard CRDs |
| [kagenti-extensions](https://github.com/kagenti/kagenti-extensions) | Extensions and integrations for the Kagenti platform |

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes and commit with conventional commit messages
4. Open a pull request against `main`
