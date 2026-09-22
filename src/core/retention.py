from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# What a policy says about one memory layer.
KEEP_FOREVER = "keep"


@dataclass(frozen=True)
class RetentionPolicy:
    """How long one tenant's memories may be kept, by layer.

    This is a different thing from the garbage collector's TTL, and the
    difference is the whole reason it exists. A TTL is a heuristic for
    forgetting what nobody uses: it measures idleness, and it stretches for
    important or pinned items, because keeping a useful memory longer is a
    feature.

    A retention policy is a promise to someone that their data will be gone by
    a certain date. It measures age, not idleness, and nothing stretches it —
    not importance, not pinning, not recent access. A commitment that quietly
    exempts the records someone marked important is not a commitment.
    """

    prefix: str
    #: layer name -> days to keep, or KEEP_FOREVER for "age never expires this"
    layers: dict[str, float | str]

    def matches(self, project: str | None) -> bool:
        return bool(project) and project.startswith(self.prefix)  # type: ignore[union-attr]

    def days_for(self, memory_layer: str) -> float | str | None:
        """Days to keep, KEEP_FOREVER, or None when this policy says nothing."""
        return self.layers.get(memory_layer)


def parse_retention_policies(raw: str) -> list[RetentionPolicy]:
    """Read `GC_RETENTION_POLICIES` — a JSON array of per-prefix policies.

    ```json
    [{"prefix": "cx-", "layers": {"L1": 90, "L2": 90, "L3": "keep"}}]
    ```

    A malformed entry is dropped with a warning. Dropping it means that
    tenant falls back to the ordinary TTL rather than the promised ceiling, so
    the warning matters: it is the difference between "kept 90 days" and "kept
    until the collector felt like it".
    """
    if not raw.strip():
        return []

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("GC_RETENTION_POLICIES is not valid JSON — no policies loaded")
        return []

    if not isinstance(entries, list):
        logger.error("GC_RETENTION_POLICIES must be a JSON array — no policies loaded")
        return []

    policies: list[RetentionPolicy] = []
    for index, entry in enumerate(entries):
        policy = _parse_entry(entry, index)
        if policy is not None:
            policies.append(policy)

    # Longest prefix first, so a specific policy wins over a broader one.
    policies.sort(key=lambda p: len(p.prefix), reverse=True)
    return policies


def _parse_entry(entry: Any, index: int) -> RetentionPolicy | None:
    if not isinstance(entry, dict):
        logger.error("GC_RETENTION_POLICIES[%d] is not an object — skipped", index)
        return None

    prefix = str(entry.get("prefix") or "")
    if not prefix:
        logger.error("GC_RETENTION_POLICIES[%d] has no prefix — skipped", index)
        return None

    raw_layers = entry.get("layers")
    if not isinstance(raw_layers, dict) or not raw_layers:
        logger.error("Retention policy %r has no layers — skipped", prefix)
        return None

    layers: dict[str, float | str] = {}
    for layer, value in raw_layers.items():
        name = str(layer).upper()
        if isinstance(value, str) and value.lower() == KEEP_FOREVER:
            layers[name] = KEEP_FOREVER
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            layers[name] = float(value)
        else:
            logger.error(
                "Retention policy %r has an unusable value for %s (%r) — that layer is skipped",
                prefix, name, value,
            )

    if not layers:
        return None
    return RetentionPolicy(prefix=prefix, layers=layers)


def policy_for(policies: list[RetentionPolicy], project: str | None) -> RetentionPolicy | None:
    for policy in policies:
        if policy.matches(project):
            return policy
    return None
