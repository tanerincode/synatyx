from __future__ import annotations

from src.core import citations


def test_a_crawled_url_is_a_citation_link():
    assert citations.citation_url({"url": "https://help.example.test/a"}) == "https://help.example.test/a"
    assert citations.citation_url({"source": "http://help.example.test/b"}) == "http://help.example.test/b"


def test_a_file_path_is_never_offered_as_a_link():
    # `source` holds a path as often as a URL. Handing a path back as a
    # citation puts a dead link in someone's UI.
    assert citations.citation_url({"source": "/tmp/help.md"}) is None
    assert citations.citation_url({"source": "help-42"}) is None
    assert citations.citation_url({}) is None


def test_url_wins_over_source_when_both_are_present():
    metadata = {"url": "https://canonical.test/a", "source": "https://fetched.test/b"}

    assert citations.citation_url(metadata) == "https://canonical.test/a"


def test_offsets_need_both_ends_to_mean_anything():
    assert citations.offsets({"offset_start": 0, "offset_end": 40}) == {"start": 0, "end": 40}
    assert citations.offsets({"offset_start": 0}) is None
    assert citations.offsets({"offset_start": "0", "offset_end": "40"}) is None
    assert citations.offsets({}) is None


def test_citation_always_has_the_same_keys():
    # A caller must be able to tell "no title" from "this build sends no
    # titles", so the keys are present and null rather than absent.
    assert citations.citation({}) == {
        "sourceId": None,
        "url": None,
        "title": None,
        "offsets": None,
    }


def test_a_section_heading_stands_in_for_a_missing_title():
    assert citations.citation({"section": "Refunds"})["title"] == "Refunds"
    assert citations.citation({"title": "Billing", "section": "Refunds"})["title"] == "Billing"


def test_annotate_adds_citation_to_a_dumped_item():
    item = {"id": "c1", "content": "…", "metadata": {"source_id": "help-42"}}

    assert citations.annotate(item)["citation"]["sourceId"] == "help-42"
