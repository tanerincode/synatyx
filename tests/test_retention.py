from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import GCSettings
from src.core.gc import GarbageCollector
from src.core.retention import KEEP_FOREVER, parse_retention_policies, policy_for

CX_POLICY = json.dumps([{"prefix": "cx-", "layers": {"L1": 90, "L2": 90, "L3": "keep"}}])

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _collector(policies: str = CX_POLICY) -> GarbageCollector:
    return GarbageCollector(
        qdrant=None,  # type: ignore[arg-type]
        postgres=None,  # type: ignore[arg-type]
        settings=GCSettings(retention_policies=policies),
    )


def _item(**overrides: Any) -> dict[str, Any]:
    item = {
        "_id": "i1",
        "project": "cx-lens-ai",
        "memory_layer": "L2",
        "created_at": (NOW - timedelta(days=10)).isoformat(),
        "importance": 0.5,
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_a_policy_reads_days_and_keep():
    policy = parse_retention_policies(CX_POLICY)[0]

    assert policy.days_for("L2") == 90.0
    assert policy.days_for("L3") == KEEP_FOREVER
    assert policy.days_for("L4") is None


def test_a_longer_prefix_wins_over_a_broader_one():
    policies = parse_retention_policies(json.dumps([
        {"prefix": "cx-", "layers": {"L2": 90}},
        {"prefix": "cx-lens", "layers": {"L2": 7}},
    ]))

    assert policy_for(policies, "cx-lens-ai").days_for("L2") == 7.0
    assert policy_for(policies, "cx-other").days_for("L2") == 90.0


def test_unusable_values_drop_the_layer_not_the_policy():
    policy = parse_retention_policies(json.dumps([
        {"prefix": "cx-", "layers": {"L2": 90, "L3": -1, "L1": "forever"}}
    ]))[0]

    assert policy.days_for("L2") == 90.0
    assert policy.days_for("L3") is None
    assert policy.days_for("L1") is None


def test_a_malformed_document_yields_no_policies():
    assert parse_retention_policies("{not json") == []
    assert parse_retention_policies('[{"layers": {"L2": 1}}]') == []
    assert parse_retention_policies('[{"prefix": "cx-"}]') == []


def test_projects_outside_every_prefix_have_no_policy():
    assert policy_for(parse_retention_policies(CX_POLICY), "taty-v2") is None
    assert policy_for(parse_retention_policies(CX_POLICY), None) is None


# ---------------------------------------------------------------------------
# The verdict the collector acts on
# ---------------------------------------------------------------------------

def test_conversation_memory_inside_the_window_is_kept():
    assert _collector()._retention_verdict(_item(), NOW) == "keep"


def test_conversation_memory_past_the_window_expires():
    verdict = _collector()._retention_verdict(
        _item(created_at=(NOW - timedelta(days=91)).isoformat()), NOW
    )

    assert verdict is not None and "older than 90 days" in verdict


def test_knowledge_never_expires_by_age():
    # L3 under the same prefix is marked "keep": it goes only by deprecation.
    verdict = _collector()._retention_verdict(
        _item(memory_layer="L3", created_at=(NOW - timedelta(days=4000)).isoformat()), NOW
    )

    assert verdict == "keep"


def test_age_is_measured_from_creation_not_from_last_access():
    # Re-reading a conversation on day 89 must not extend the promise.
    verdict = _collector()._retention_verdict(
        _item(
            created_at=(NOW - timedelta(days=120)).isoformat(),
            last_accessed_at=NOW.isoformat(),
        ),
        NOW,
    )

    assert verdict is not None and verdict != "keep"


def test_importance_and_pinning_do_not_extend_a_retention_window():
    # The ordinary TTL stretches for important items. A promise that data is
    # gone after 90 days cannot make an exception for the records someone
    # marked important — those are the ones a person asking would care about.
    verdict = _collector()._retention_verdict(
        _item(
            created_at=(NOW - timedelta(days=91)).isoformat(),
            importance=1.0,
            is_pinned=True,
        ),
        NOW,
    )

    assert verdict is not None and verdict != "keep"


def test_an_item_with_no_creation_date_is_expired_rather_than_kept():
    verdict = _collector()._retention_verdict(_item(created_at=None), NOW)

    assert verdict is not None and "no creation date" in verdict


def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing():
    naive = (NOW - timedelta(days=91)).replace(tzinfo=None).isoformat()

    verdict = _collector()._retention_verdict(_item(created_at=naive), NOW)

    assert verdict is not None and verdict != "keep"


def test_a_project_with_no_policy_keeps_the_ordinary_ttl_behaviour():
    # None means "no opinion", which leaves the item to the normal TTL path.
    assert _collector()._retention_verdict(_item(project="taty-v2"), NOW) is None


def test_a_layer_the_policy_does_not_mention_is_left_to_the_ttl():
    assert _collector()._retention_verdict(_item(memory_layer="L4"), NOW) is None


def test_no_configured_policies_changes_nothing():
    assert _collector(policies="")._retention_verdict(_item(), NOW) is None
