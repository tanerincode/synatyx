from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer

# Default token budget per layer (doc section 2.4)
DEFAULT_TOTAL_BUDGET = 128_000


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — the single estimator used
    everywhere so budgets and usage metering stay comparable."""
    return len(text) // 4

FIXED_ALLOCATIONS: dict[str, int] = {
    "system_prompt": 2_000,
    "current_message": 1_000,
    "response_headroom": 4_000,
}

LAYER_ALLOCATIONS: dict[MemoryLayer, int] = {
    MemoryLayer.L4: 500,
    MemoryLayer.L2: 1_000,
    MemoryLayer.L3: 2_000,
    MemoryLayer.L1: 4_000,
}


@dataclass
class BudgetAllocation:
    system_prompt: int
    current_message: int
    response_headroom: int
    l1_working: int
    l2_episodic: int
    l3_semantic: int
    l4_procedural: int
    total_available: int
    total_used: int

    @property
    def remaining(self) -> int:
        return self.total_available - self.total_used

    def to_dict(self) -> dict[str, int]:
        return {
            "system_prompt": self.system_prompt,
            "current_message": self.current_message,
            "response_headroom": self.response_headroom,
            "L1": self.l1_working,
            "L2": self.l2_episodic,
            "L3": self.l3_semantic,
            "L4": self.l4_procedural,
            "total_available": self.total_available,
            "total_used": self.total_used,
            "remaining": self.remaining,
        }


class BudgetManager:
    def __init__(self, total_budget: int = DEFAULT_TOTAL_BUDGET) -> None:
        self.total_budget = total_budget

    def get_layer_limit(self, layer: MemoryLayer) -> int:
        return LAYER_ALLOCATIONS[layer]

    def get_allocation(self) -> BudgetAllocation:
        fixed = sum(FIXED_ALLOCATIONS.values())
        layers = sum(LAYER_ALLOCATIONS.values())
        return BudgetAllocation(
            system_prompt=FIXED_ALLOCATIONS["system_prompt"],
            current_message=FIXED_ALLOCATIONS["current_message"],
            response_headroom=FIXED_ALLOCATIONS["response_headroom"],
            l1_working=LAYER_ALLOCATIONS[MemoryLayer.L1],
            l2_episodic=LAYER_ALLOCATIONS[MemoryLayer.L2],
            l3_semantic=LAYER_ALLOCATIONS[MemoryLayer.L3],
            l4_procedural=LAYER_ALLOCATIONS[MemoryLayer.L4],
            total_available=self.total_budget,
            total_used=fixed + layers,
        )

    def enforce(
        self,
        items: list[ContextItem],
        layer: MemoryLayer | None = None,
    ) -> list[ContextItem]:
        """
        Trim items list to fit within the token budget for the given layer.
        Items are assumed to already be sorted by score descending.
        Pinned items are always kept first.
        """
        limit = LAYER_ALLOCATIONS[layer] if layer else sum(LAYER_ALLOCATIONS.values())

        pinned = [i for i in items if i.is_pinned]
        rest = [i for i in items if not i.is_pinned]

        selected: list[ContextItem] = []
        used = 0

        for item in pinned + rest:
            tokens = item.token_estimate
            if used + tokens > limit:
                break
            selected.append(item)
            used += tokens

        return selected

    def estimate_tokens(self, items: list[ContextItem]) -> int:
        return sum(i.token_estimate for i in items)


# ---------------------------------------------------------------------------
# Section-based budgeting — shared by brief (spillover off) and pack
# (spillover on). One packer instead of the three ad-hoc ones that grew in
# brief._fit, BudgetManager.enforce, and the dispatch-layer estimates.
# ---------------------------------------------------------------------------

@dataclass
class BudgetEntry:
    """One packable unit.

    Decoupled from ContextItem so Postgres rows (tasks, skills) and index hits
    participate in the same budget. `payload` may be a callable so expensive
    serialization (staleness file hashing) only runs for entries that fit.
    `raw_content` enables truncating an oversized first entry.
    """

    tokens: int
    payload: dict[str, Any] | Callable[[], dict[str, Any]]
    raw_content: str | None = None
    truncatable: bool = True

    def materialize(self) -> dict[str, Any]:
        return self.payload() if callable(self.payload) else dict(self.payload)


@dataclass
class SectionResult:
    entries: list[dict[str, Any]] = field(default_factory=list)
    used: int = 0
    allocated: int = 0
    overflow: int = 0  # entries that did not fit


def fit_entries(entries: list[BudgetEntry], budget_tokens: int) -> SectionResult:
    """Greedily pack entries into a token budget, preserving order.

    If even the first entry doesn't fit and it is truncatable, it is included
    truncated to budget_tokens * 4 chars — a section with content should never
    come back empty just because one item is long.
    """
    selected: list[dict[str, Any]] = []
    used = 0
    included = 0
    for entry in entries:
        if used + entry.tokens > budget_tokens:
            if not selected and budget_tokens > 0 and entry.truncatable and entry.raw_content is not None:
                payload = entry.materialize()
                payload["content"] = entry.raw_content[: budget_tokens * 4].rstrip() + "…"
                payload["truncated"] = True
                selected.append(payload)
                used = budget_tokens
                included += 1
            break
        selected.append(entry.materialize())
        used += entry.tokens
        included += 1
    return SectionResult(
        entries=selected,
        used=used,
        allocated=budget_tokens,
        overflow=len(entries) - included,
    )


class SectionBudgeter:
    """Split a total token budget across weighted sections, with optional
    spillover: unspent budget from underfull sections is redistributed to
    sections that overflowed, proportional to their weights (one round,
    deterministic)."""

    def __init__(self, max_tokens: int, weights: Mapping[str, float]) -> None:
        self.max_tokens = max_tokens
        self.weights = dict(weights)

    def allocate(self, active_sections: Iterable[str] | None = None) -> dict[str, int]:
        """Per-section budgets. When only a subset of sections is active,
        weights are renormalized over that subset so no budget is lost."""
        active = list(active_sections) if active_sections is not None else list(self.weights)
        total_weight = sum(self.weights[s] for s in active if s in self.weights)
        if total_weight <= 0:
            return {s: 0 for s in active}
        return {
            s: int(self.max_tokens * self.weights.get(s, 0) / total_weight)
            for s in active
        }

    def pack(
        self,
        sections: dict[str, list[BudgetEntry]],
        spillover: bool = True,
    ) -> dict[str, SectionResult]:
        budgets = self.allocate(sections.keys())
        results = {name: fit_entries(entries, budgets[name]) for name, entries in sections.items()}

        if spillover:
            pool = sum(r.allocated - r.used for r in results.values())
            starved = [name for name, r in results.items() if r.overflow > 0]
            starved_weight = sum(self.weights.get(s, 0) for s in starved)
            if pool > 0 and starved and starved_weight > 0:
                for name in starved:
                    extra = int(pool * self.weights.get(name, 0) / starved_weight)
                    if extra > 0:
                        results[name] = fit_entries(sections[name], budgets[name] + extra)
        return results

