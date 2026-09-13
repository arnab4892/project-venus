"""Per-area multi-fetch (station task): ``ground_additional_areas`` deliberately fetches each
extra product area a turn engages, capped and fail-open, so an area's facts are grounded by a
fetch rather than retrieval co-occurrence. Verified here without a DB — a fake tools stub returns
canned resolver verdicts and records the calls (English ⇒ ``english_query`` needs no LLM seam)."""

from __future__ import annotations

from agentkit.runtime.agents.base import _MAX_EXTRA_AREA_FETCHES, ground_additional_areas
from agentkit.runtime.ops import ToolCallRecord


class _FakeTools:
    def __init__(self, verdicts: dict[str, str]):
        self._verdicts = verdicts  # area -> confident_env | confident_prod | ambiguous | none | raise
        self.records: list[ToolCallRecord] = []
        self.get_calls: list[str] = []
        self.search_calls: list[dict] = []
        self._n = 0

    def get_product(self, name: str) -> ToolCallRecord:
        self.get_calls.append(name)
        kind = self._verdicts.get(name, "none")
        if kind == "raise":
            raise RuntimeError("resolver boom")
        self._n += 1
        fam = f"fam.{name.replace(' ', '_')}"
        if kind == "confident_env":
            res = {"matched_by": "family_envelope", "products": [],
                   "family": {"family_id": fam}, "capabilities": []}
        elif kind == "confident_prod":
            res = {"matched_by": "family_contains", "products": [{"family_id": fam}]}
        elif kind == "ambiguous":
            res = {"matched_by": "ambiguous", "products": [], "candidates": ["A", "B"]}
        else:
            res = {"matched_by": None, "products": []}
        rec = ToolCallRecord(f"tr{self._n}", "get_product", {"model_or_family": name}, res, 0, 1)
        self.records.append(rec)
        return rec

    def search_documents(self, query, *, family_ids=None, division=None, k=3):
        self.search_calls.append({"family_ids": family_ids, "division": division})


def _run(tools, areas):
    return ground_additional_areas(tools, None, "latest user", "en", areas, division="industrial")


def test_confident_area_is_fetched_and_family_scoped_searched():
    tools = _FakeTools({"hydrogen fuelling systems": "confident_env"})
    fetched, skipped = _run(tools, ["hydrogen fuelling systems"])
    assert [r.tr_id for r in fetched] == ["tr1"] and skipped == []
    assert tools.search_calls == [{"family_ids": ["fam.hydrogen_fuelling_systems"], "division": None}]


def test_ambiguous_area_is_skipped_and_traced_not_asked():
    tools = _FakeTools({"cng boosters": "ambiguous"})
    fetched, skipped = _run(tools, ["cng boosters"])
    assert fetched == []
    assert skipped == [{"area": "cng boosters", "candidates": ["A", "B"]}]
    assert tools.search_calls == []  # ambiguous → no fetch, no scoped search


def test_unresolved_area_is_skipped():
    tools = _FakeTools({"widget": "none"})
    fetched, skipped = _run(tools, ["widget"])
    assert fetched == [] and skipped == [{"area": "widget", "candidates": None}]


def test_fetch_error_fails_open_and_continues():
    tools = _FakeTools({"boom": "raise", "natural gas compressors": "confident_env"})
    fetched, skipped = _run(tools, ["boom", "natural gas compressors"])
    # the raising area is skipped (not recorded), the next area still grounds — never crashes
    assert [r.tr_id for r in fetched] == ["tr1"]
    assert skipped == []  # a raise is logged, not recorded as a skip verdict


def test_cap_limits_confident_fetches():
    areas = [f"line {i}" for i in range(_MAX_EXTRA_AREA_FETCHES + 2)]
    tools = _FakeTools({a: "confident_prod" for a in areas})
    fetched, _ = _run(tools, areas)
    assert len(fetched) == _MAX_EXTRA_AREA_FETCHES
    assert len(tools.search_calls) == _MAX_EXTRA_AREA_FETCHES


def test_duplicate_areas_deduped():
    tools = _FakeTools({"natural gas compressors": "confident_env"})
    fetched, _ = _run(tools, ["natural gas compressors", "Natural Gas Compressors", "  natural gas compressors  "])
    assert len(fetched) == 1 and tools.get_calls == ["natural gas compressors"]
