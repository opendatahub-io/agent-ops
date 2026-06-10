"""Kubernetes-native agent discovery via AgentCard CRDs.

When running in-cluster the orchestrator can discover peer agents by
querying the Kubernetes API for ``AgentCard`` custom resources
(``agent.kagenti.dev/v1alpha1``) created by the kagenti operator.

Agents with ``protocol.kagenti.io/a2a: ""`` label are automatically
discovered.

No additional Python dependencies are required — this module uses
``httpx`` (already a project dependency) to make raw REST calls to the
Kubernetes API, authenticating with the mounted ServiceAccount token.
"""

from __future__ import annotations

import logging
import ssl
from os import getenv
from pathlib import Path

import httpx
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from redbank_orchestrator.discovery import PeerAgent, _slugify

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_SA_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
_SA_NS_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")

_K8S_API_BASE = "https://kubernetes.default.svc"
_AGENTCARD_API = "apis/agent.kagenti.dev/v1alpha1"


# ── Public helpers ───────────────────────────────────────────────────────────


def is_in_cluster() -> bool:
    """Return ``True`` when running inside a Kubernetes pod."""
    return _SA_TOKEN_PATH.exists()


async def discover_from_k8s(
    *,
    timeout: float = 15.0,
) -> list[PeerAgent]:
    """Discover peer agents by listing AgentCard CRDs via the Kubernetes API.

    Namespace is auto-detected from the ServiceAccount mount.  The
    orchestrator's own AgentCard is excluded using ``AGENT_SELF_NAME``
    (injected by the Helm chart) or by parsing the pod hostname.

    Args:
        timeout: HTTP timeout for the Kubernetes API call.

    Returns:
        A list of ``PeerAgent`` objects for every valid A2A AgentCard found.
    """
    if not is_in_cluster():
        logger.debug("Not running in-cluster — skipping K8s discovery.")
        return []

    # Resolve namespace from SA mount
    ns = _read_file(_SA_NS_PATH)
    if not ns:
        logger.warning("Cannot determine namespace for K8s agent discovery.")
        return []

    # Resolve self-name for filtering
    self_name = getenv("AGENT_SELF_NAME") or _self_deployment_name()

    # Read ServiceAccount credentials
    token = _read_file(_SA_TOKEN_PATH)
    if not token:
        logger.warning("ServiceAccount token not found — cannot query K8s API.")
        return []

    # Build TLS context with the cluster CA
    ssl_ctx = _build_ssl_context()

    url = f"{_K8S_API_BASE}/{_AGENTCARD_API}/namespaces/{ns}/agentcards"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(verify=ssl_ctx, timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "K8s API returned %s for AgentCard list: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return []
    except Exception as exc:
        logger.warning("K8s API request failed: %s", exc)
        return []

    data = resp.json()
    items: list[dict] = data.get("items", [])
    if not items:
        logger.info("No AgentCard CRDs found in namespace %s.", ns)
        return []

    peers: list[PeerAgent] = []
    seen_names: set[str] = set()

    for item in items:
        peer = _agentcard_to_peer(item, ns, self_name)
        if peer is None:
            continue
        # Deduplicate tool names
        if peer.tool_name in seen_names:
            peer.tool_name = f"{peer.tool_name}_{len(seen_names)}"
        seen_names.add(peer.tool_name)
        peers.append(peer)

    logger.info(
        "K8s discovery: found %d AgentCard CRDs, resolved %d peers in namespace %s.",
        len(items),
        len(peers),
        ns,
    )
    return peers


# ── Internal helpers ─────────────────────────────────────────────────────────


def _read_file(path: Path) -> str | None:
    """Read a file's content, stripping whitespace.  Returns None on failure."""
    try:
        return path.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return None


def _build_ssl_context() -> ssl.SSLContext:
    """Create an SSL context trusting the in-cluster CA bundle."""
    ctx = ssl.create_default_context()
    if _SA_CA_PATH.exists():
        ctx.load_verify_locations(str(_SA_CA_PATH))
    return ctx


def _self_deployment_name() -> str | None:
    """Best-effort guess at the owning Deployment name from the pod name.

    Kubernetes pod names follow ``{deployment}-{replicaset-hash}-{pod-hash}``
    so stripping the last two ``-`` segments recovers the Deployment name.
    """
    hostname = getenv("HOSTNAME")
    if not hostname:
        return None
    parts = hostname.rsplit("-", 2)
    return parts[0] if len(parts) >= 3 else hostname


def _agentcard_to_peer(
    item: dict,
    namespace: str,
    self_name: str | None,
) -> PeerAgent | None:
    """Convert a single AgentCard CRD JSON object into a PeerAgent.

    Returns ``None`` if the card is not usable (no cached data, wrong
    protocol, or it's the orchestrator's own card).
    """
    metadata = item.get("metadata", {})
    cr_name = metadata.get("name", "<unknown>")
    status = item.get("status", {})
    spec = item.get("spec", {})

    # Must have a cached card in status
    card_data = status.get("card")
    if not card_data:
        logger.debug("AgentCard %s has no cached card data — skipping.", cr_name)
        return None

    # Must be an A2A agent
    protocol = status.get("protocol", "")
    if protocol and protocol != "a2a":
        logger.debug(
            "AgentCard %s uses protocol %r, not a2a — skipping.", cr_name, protocol
        )
        return None

    # Determine the target workload name
    target_ref = spec.get("targetRef") or status.get("targetRef") or {}
    target_name = target_ref.get("name", "")

    # Skip our own card
    if self_name and target_name and target_name == self_name:
        logger.debug("AgentCard %s targets self (%s) — skipping.", cr_name, self_name)
        return None

    # Use the URL straight from the agent card — it already has the correct
    # host, port, and path as reported by the agent itself.
    service_url = card_data.get("url", "").rstrip("/")
    if not service_url:
        logger.debug("AgentCard %s has no URL in card data — skipping.", cr_name)
        return None

    # Construct an a2a.types.AgentCard from the CRD status data
    agent_card = _build_a2a_card(card_data, service_url)

    return PeerAgent(url=service_url, card=agent_card)


def _build_a2a_card(card_data: dict, service_url: str) -> AgentCard:
    """Build an ``a2a.types.AgentCard`` from the CRD status card data."""
    skills = []
    for s in card_data.get("skills", []):
        skills.append(
            AgentSkill(
                id=s.get("id", ""),
                name=s.get("name", ""),
                description=s.get("description", ""),
                tags=s.get("tags", []),
                examples=s.get("examples", []),
            )
        )

    caps_data = card_data.get("capabilities") or {}
    capabilities = AgentCapabilities(
        streaming=caps_data.get("streaming", False),
    )

    return AgentCard(
        name=card_data.get("name", ""),
        description=card_data.get("description", ""),
        url=service_url + "/",
        version=card_data.get("version", ""),
        capabilities=capabilities,
        skills=skills,
        defaultInputModes=card_data.get("defaultInputModes", ["text"]),
        defaultOutputModes=card_data.get("defaultOutputModes", ["text"]),
    )
