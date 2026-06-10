"""A2A peer discovery via Kubernetes AgentCard CRDs.

The orchestrator discovers peer agents by querying ``AgentCard`` CRDs
(``agent.kagenti.dev/v1alpha1``) created by the kagenti operator.
Agents with ``protocol.kagenti.io/a2a: ""`` label are automatically
discovered — no manual configuration required.

Re-discovery runs every 15 seconds so new agents are picked up
automatically without restarting the orchestrator.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── Public helpers ───────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Turn an agent name like 'Knowledge Agent' into 'ask_knowledge_agent'."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # Prefix with ask_ for readability unless already present
    if not slug.startswith("ask_"):
        slug = f"ask_{slug}"
    return slug


class PeerAgent:
    """Resolved metadata for a single downstream A2A agent."""

    def __init__(self, url: str, card) -> None:
        self.url = url
        self.card = card
        self.tool_name = _slugify(card.name)

    def __repr__(self) -> str:
        return f"PeerAgent(name={self.card.name!r}, url={self.url!r})"


def peers_changed(old: list[PeerAgent], new: list[PeerAgent]) -> bool:
    """Return True if the set of peers has changed (by name + url)."""
    old_set = {(p.card.name, p.url) for p in old}
    new_set = {(p.card.name, p.url) for p in new}
    return old_set != new_set


# ── Main entry point ─────────────────────────────────────────────────────────


async def discover_peers(
    *,
    timeout: float = 15.0,
) -> list[PeerAgent]:
    """Discover peer agents via Kubernetes AgentCard CRDs.

    Queries the Kubernetes API for ``AgentCard`` custom resources.
    Outside a cluster (local dev), returns an empty list.

    Args:
        timeout: Per-request HTTP timeout.

    Returns:
        A list of successfully discovered ``PeerAgent`` objects.
        Peers that fail to respond are logged and skipped.
    """
    peers: list[PeerAgent] = []
    try:
        from redbank_orchestrator.k8s_discovery import discover_from_k8s, is_in_cluster

        if is_in_cluster():
            peers = await discover_from_k8s(timeout=timeout)
            if peers:
                logger.info(
                    "Discovered %d peers via K8s AgentCard CRDs.",
                    len(peers),
                )
    except Exception as exc:
        logger.debug("K8s discovery unavailable (expected outside cluster): %s", exc)

    if not peers:
        logger.warning("No peer agents discovered — orchestrator has no tools.")
    return peers
