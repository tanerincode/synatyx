from enum import Enum


class MemoryLayer(str, Enum):
    """
    Memory layer hierarchy for the Synatyx Context Engine.

    L1 — Working Memory   : Last 10-20 messages. Always included. ~2k-4k tokens.
    L2 — Episodic Memory  : Session summaries. ~500-1k tokens.
    L3 — Semantic Memory  : Vector similarity search from past conversations. ~1k-2k tokens.
    L4 — Procedural Memory: Permanent user preferences and rules. ~200-500 tokens.
    """

    L1 = "L1"  # Working memory
    L2 = "L2"  # Episodic memory
    L3 = "L3"  # Semantic memory
    L4 = "L4"  # Procedural memory

    @property
    def token_budget(self) -> int:
        budgets = {
            MemoryLayer.L1: 4000,
            MemoryLayer.L2: 1000,
            MemoryLayer.L3: 2000,
            MemoryLayer.L4: 500,
        }
        return budgets[self]

    @property
    def description(self) -> str:
        descriptions = {
            MemoryLayer.L1: "Working memory — recent messages",
            MemoryLayer.L2: "Episodic memory — session summaries",
            MemoryLayer.L3: "Semantic memory — vector similarity search",
            MemoryLayer.L4: "Procedural memory — permanent user preferences",
        }
        return descriptions[self]

