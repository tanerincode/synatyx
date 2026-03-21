from __future__ import annotations

from dataclasses import dataclass

from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer

# Default token budget per layer (doc section 2.4)
DEFAULT_TOTAL_BUDGET = 128_000

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

