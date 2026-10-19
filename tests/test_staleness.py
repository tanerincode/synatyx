from __future__ import annotations

from pathlib import Path

from src.config import GCSettings
from src.core.gc import GarbageCollector
from src.core.staleness import annotate_staleness, check_stale_files, compute_file_hashes

# ── file hashes ──────────────────────────────────────────────────────────────

def test_compute_hashes_and_fresh_check(tmp_path: Path) -> None:
    f = tmp_path / "config.py"
    f.write_text("PORT = 6333")
    hashes = compute_file_hashes([str(f)])
    assert str(f) in hashes and len(hashes[str(f)]) == 16
    assert check_stale_files({"file_hashes": hashes}) == []


def test_changed_file_flagged_stale(tmp_path: Path) -> None:
    f = tmp_path / "config.py"
    f.write_text("PORT = 6333")
    hashes = compute_file_hashes([str(f)])
    f.write_text("PORT = 7777")
    assert check_stale_files({"file_hashes": hashes}) == [str(f)]


def test_deleted_file_flagged_stale(tmp_path: Path) -> None:
    f = tmp_path / "gone.py"
    f.write_text("x = 1")
    hashes = compute_file_hashes([str(f)])
    f.unlink()
    assert check_stale_files({"file_hashes": hashes}) == [str(f)]


def test_unreadable_paths_skipped_at_store_time(tmp_path: Path) -> None:
    assert compute_file_hashes([str(tmp_path / "missing.py"), "", 42]) == {}  # type: ignore[list-item]


def test_no_hashes_means_never_stale() -> None:
    assert check_stale_files({}) == []
    assert check_stale_files({"file_hashes": {}}) == []


def test_annotate_adds_flags_only_when_stale(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("v1")
    hashes = compute_file_hashes([str(f)])

    fresh = annotate_staleness({"metadata": {"file_hashes": hashes}})
    assert "possibly_stale" not in fresh

    f.write_text("v2")
    stale = annotate_staleness({"metadata": {"file_hashes": hashes}})
    assert stale["possibly_stale"] is True
    assert stale["stale_files"] == [str(f)]


# ── type-aware TTL decay ─────────────────────────────────────────────────────

def _gc() -> GarbageCollector:
    return GarbageCollector(qdrant=None, postgres=None, settings=GCSettings())  # type: ignore[arg-type]


def test_untyped_item_keeps_classic_ttl() -> None:
    gc = _gc()
    item = {"importance": 0.5, "metadata": {}}
    assert gc.effective_ttl(30, item) == 30 * (1 + 0.5 * 3.0)


def test_file_location_facts_decay_fast() -> None:
    gc = _gc()
    typed = {"importance": 0.5, "metadata": {"fact_type": "file-location"}}
    untyped = {"importance": 0.5, "metadata": {}}
    assert gc.effective_ttl(30, typed) == gc.effective_ttl(30, untyped) * 0.3


def test_preferences_decay_slowly() -> None:
    gc = _gc()
    item = {"importance": 0.5, "metadata": {"fact_type": "preference"}}
    assert gc.effective_ttl(30, item) == 30 * (1 + 0.5 * 3.0) * 3.0


def test_unknown_fact_type_is_neutral() -> None:
    gc = _gc()
    item = {"importance": 0.5, "metadata": {"fact_type": "something-new"}}
    assert gc.effective_ttl(30, item) == 30 * (1 + 0.5 * 3.0)
