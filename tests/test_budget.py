import pytest

from src.core.budget import (
    FIXED_ALLOCATIONS,
    LAYER_ALLOCATIONS,
    BudgetAllocation,
    BudgetEntry,
    BudgetManager,
    SectionBudgeter,
    fit_entries,
)
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer


def _make_item(
    content: str,
    layer: MemoryLayer = MemoryLayer.L3,
    is_pinned: bool = False,
) -> ContextItem:
    return ContextItem(
        user_id="user-1",
        content=content,
        memory_layer=layer,
        is_pinned=is_pinned,
    )


@pytest.fixture
def manager():
    return BudgetManager()


# ── Layer limits ──────────────────────────────────────────────────────────────

def test_layer_limits_match_allocations(manager):
    for layer in MemoryLayer:
        assert manager.get_layer_limit(layer) == LAYER_ALLOCATIONS[layer]


def test_l1_limit(manager):
    assert manager.get_layer_limit(MemoryLayer.L1) == 4000


def test_l2_limit(manager):
    assert manager.get_layer_limit(MemoryLayer.L2) == 1000


def test_l3_limit(manager):
    assert manager.get_layer_limit(MemoryLayer.L3) == 2000


def test_l4_limit(manager):
    assert manager.get_layer_limit(MemoryLayer.L4) == 500


# ── get_allocation ────────────────────────────────────────────────────────────

def test_allocation_returns_correct_type(manager):
    alloc = manager.get_allocation()
    assert isinstance(alloc, BudgetAllocation)


def test_allocation_total_used_is_sum(manager):
    alloc = manager.get_allocation()
    expected = sum(FIXED_ALLOCATIONS.values()) + sum(LAYER_ALLOCATIONS.values())
    assert alloc.total_used == expected


def test_allocation_remaining(manager):
    alloc = manager.get_allocation()
    assert alloc.remaining == alloc.total_available - alloc.total_used


def test_allocation_to_dict_keys(manager):
    d = manager.get_allocation().to_dict()
    for key in ["L1", "L2", "L3", "L4", "system_prompt", "total_available", "remaining"]:
        assert key in d


# ── enforce ───────────────────────────────────────────────────────────────────

def test_enforce_empty_list(manager):
    assert manager.enforce([], MemoryLayer.L3) == []


def test_enforce_fits_within_budget(manager):
    # Each item ~25 tokens (100 chars), limit=2000 for L3 → all 10 fit
    items = [_make_item("x" * 100, MemoryLayer.L3) for _ in range(10)]
    result = manager.enforce(items, MemoryLayer.L3)
    assert len(result) == 10


def test_enforce_trims_to_budget(manager):
    # Each item = 4400 chars = 1100 tokens. L3 limit = 2000 tokens.
    # 1 item = 1100 (fits), 2 items = 2200 (exceeds) → only 1 fits
    items = [_make_item("x" * 4400, MemoryLayer.L3) for _ in range(5)]
    result = manager.enforce(items, MemoryLayer.L3)
    assert len(result) == 1


def test_enforce_pinned_items_kept_first(manager):
    # Fill budget with non-pinned items, add one pinned — pinned should survive
    non_pinned = [_make_item("x" * 900, MemoryLayer.L3) for _ in range(3)]
    pinned = _make_item("pinned content " * 10, MemoryLayer.L3, is_pinned=True)
    items = [pinned] + non_pinned
    result = manager.enforce(items, MemoryLayer.L3)
    assert any(i.is_pinned for i in result)


def test_enforce_no_layer_uses_total(manager):
    items = [_make_item("x" * 100) for _ in range(5)]
    result = manager.enforce(items)
    assert len(result) > 0


# ── estimate_tokens ───────────────────────────────────────────────────────────

def test_estimate_tokens_empty(manager):
    assert manager.estimate_tokens([]) == 0


def test_estimate_tokens_correct(manager):
    items = [_make_item("x" * 400) for _ in range(3)]
    # 400 chars // 4 = 100 tokens each, 3 items = 300
    assert manager.estimate_tokens(items) == 300



# ── fit_entries / SectionBudgeter ────────────────────────────────────────────



def _entry(content: str, **payload) -> BudgetEntry:
    return BudgetEntry(
        tokens=len(content) // 4,
        payload={"content": content, **payload},
        raw_content=content,
    )


def test_fit_entries_greedy_pack():
    entries = [_entry("a" * 400), _entry("b" * 400), _entry("c" * 400)]
    result = fit_entries(entries, 250)
    assert len(result.entries) == 2
    assert result.used == 200
    assert result.overflow == 1
    assert result.allocated == 250


def test_fit_entries_truncates_oversized_first():
    entries = [_entry("x" * 4000)]
    result = fit_entries(entries, 100)
    assert len(result.entries) == 1
    assert result.entries[0]["truncated"] is True
    assert result.entries[0]["content"].endswith("…")
    assert len(result.entries[0]["content"]) <= 401
    assert result.used == 100


def test_fit_entries_zero_budget_empty():
    result = fit_entries([_entry("x" * 400)], 0)
    assert result.entries == []
    assert result.overflow == 1


def test_fit_entries_lazy_payload_only_materialized_when_selected():
    calls = []

    def make_payload(name):
        def _p():
            calls.append(name)
            return {"content": name}
        return _p

    entries = [
        BudgetEntry(tokens=50, payload=make_payload("first"), raw_content="first"),
        BudgetEntry(tokens=500, payload=make_payload("second"), raw_content="second"),
    ]
    fit_entries(entries, 60)
    assert calls == ["first"]  # second overflowed and was never serialized


def test_section_budgeter_renormalizes_absent_sections():
    b = SectionBudgeter(1000, {"a": 0.5, "b": 0.3, "c": 0.2})
    full = b.allocate()
    subset = b.allocate(["a", "b"])
    assert full["a"] == 500
    assert subset["a"] == 625  # 0.5 / 0.8
    assert subset["b"] == 375


def test_section_budgeter_spillover_redistributes():
    b = SectionBudgeter(1000, {"a": 0.5, "b": 0.5})
    sections = {
        "a": [_entry("x" * 40)],                      # uses 10 of 500
        "b": [_entry("y" * 1600) for _ in range(3)],  # 400 each — overflows 500
    }
    with_spill = b.pack(sections, spillover=True)
    without = b.pack(sections, spillover=False)
    assert len(without["b"].entries) == 1
    assert len(with_spill["b"].entries) > len(without["b"].entries)
    # total never exceeds the overall budget
    assert sum(r.used for r in with_spill.values()) <= 1000


def test_section_budgeter_spillover_off_matches_base_allocation():
    b = SectionBudgeter(1000, {"a": 0.5, "b": 0.5})
    sections = {"a": [_entry("x" * 40)], "b": [_entry("y" * 4000)]}
    results = b.pack(sections, spillover=False)
    assert results["a"].allocated == 500
    assert results["b"].allocated == 500
