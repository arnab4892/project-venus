"""``get_product`` — LLD-TOOL-03.

Look up products by model number or family (id or name), joined to their family. Model
matching is alias-normalised so ``MCH16`` == ``MCH-16`` (spaces/hyphens stripped). A product
with no printed ``model_name`` is presented by its family name + variant/description
(LLD-EXT-06) — never a blank or an invented name.
"""

from __future__ import annotations

from agentkit.runtime.tracing import observe

import re

from sqlalchemy import Connection, text

_ALIAS_STRIP_RE = re.compile(r"[\s\-]+")
_MODEL_PREFIX_RE = re.compile(r"[a-z]+")
_MODEL_NUM_RE = re.compile(r"\d+")
# Word-preserving tokeniser for coverage scoring (LLD-TOOL-03, station task): split a name/query
# into lowercase alnum runs across whitespace / - / / / ( ) / . / & so "MCH-13/16 (Smart Series)"
# → {mch, 13, 16, smart, series}. Distinct from normalise_alias, which collapses to one blob with
# no word boundaries — token coverage needs the boundaries to tell "Smart MCH-16" from "MCH-16".
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalise_alias(value: str) -> str:
    """Strip spaces/hyphens and case-fold for model-number matching (``MCH-16`` → ``mch16``)."""
    return _ALIAS_STRIP_RE.sub("", value).lower()


def _tokens(value: str | None) -> frozenset[str]:
    """Word/number token set for coverage scoring.

    Drops length-1 non-numeric tokens (a stray 'C'/'S') as noise, and folds a single trailing
    plural 's' on words ≥4 chars ("compressors"→"compressor", "boosters"→"booster") so
    singular/plural phrasings match. Purely morphological — no domain word list. Numbers are kept
    verbatim ("16" stays "16").
    """
    if not value:
        return frozenset()
    out: set[str] = set()
    for t in _TOKEN_RE.findall(value.lower()):
        if len(t) < 2 and not t.isdigit():
            continue
        if len(t) >= 4 and t.endswith("s") and not t.isdigit():
            t = t[:-1]
        out.add(t)
    return frozenset(out)


def _model_tokens(name: str | None) -> tuple[str, frozenset[int]]:
    """Split a model string into its series prefix + set of model numbers.

    "MCH-16" → ("mch", {16}); "MCH – 13 / 16 ELECTRIC" → ("mch", {13, 16}); "ICON / MCH6" →
    ("icon", {6}). Used to match a queried model against its sibling variants: the same series
    (leading letters) sharing at least one model number (so "MCH-16" reaches the "13/16" range).
    """
    low = (name or "").lower()
    m = _MODEL_PREFIX_RE.match(low)
    prefix = m.group(0) if m else ""
    nums = frozenset(int(n) for n in _MODEL_NUM_RE.findall(low))
    return prefix, nums


def product_display_name(
    model_name: str | None, family_name: str | None, variant: str | None, description: str | None
) -> str:
    """Runtime display name (LLD-EXT-06): model if printed, else family + variant/description."""
    if model_name:
        return model_name
    tail = variant or (description.strip().split("\n")[0][:60] if description else None)
    base = family_name or "product"
    return f"{base} — {tail}" if tail else base


_SQL = (
    "SELECT p.product_id, p.family_id, p.model_name, p.variant, p.description, p.attributes, "
    "       p.source_doc_id, p.source_locator, "
    "       pf.name AS family_name, pf.category, pf.division, pf.summary AS family_summary "
    "FROM facts.active_product p "
    "JOIN facts.active_product_family pf ON pf.family_id = p.family_id "
    "ORDER BY p.product_id"
)

# The family registry + its published envelope (capability rows). Read straight from the family
# tables so a family carrying capability rows but NO product rows (the process/gas families) is
# still resolvable — the product-anchored ``_SQL`` above cannot see it. Same tables
# ``match_capability`` reads; figures returned verbatim.
_FAMILY_SQL = (
    "SELECT family_id, name AS family_name, category, division, summary, "
    "       source_doc_id, source_locator "
    "FROM facts.active_product_family ORDER BY family_id"
)
_CAP_SQL = (
    "SELECT cap_id, comp_type, lubricated, cooling, capacity_min, capacity_max, capacity_unit, "
    "       discharge_p_min, discharge_p_max, pressure_unit, standards, driver, "
    "       source_doc_id, source_locator "
    "FROM facts.active_capability_row WHERE family_id = :fid ORDER BY cap_id"
)
_CAP_GAS_SQL = "SELECT gas FROM facts.active_capability_gas WHERE cap_id = :cid ORDER BY gas"


def _as_product(row) -> dict:
    return {
        "product_id": row["product_id"],
        "family_id": row["family_id"],
        "display_name": product_display_name(
            row["model_name"], row["family_name"], row["variant"], row["description"]
        ),
        "model_name": row["model_name"],
        "variant": row["variant"],
        "description": row["description"],
        "family_name": row["family_name"],
        "family_summary": row["family_summary"],
        "category": row["category"],
        "division": row["division"],
        "attributes": row["attributes"],
        "source_doc_id": row["source_doc_id"],
        "source_locator": row["source_locator"],
    }


@observe(name="get_product", as_type="tool")
def get_product(conn: Connection, model_or_family: str) -> dict:
    """Return ``{"query", "matched_by", "products": [...]}`` for a model or family lookup.

    Tries an exact alias-normalised model match first; failing that, a family match by id or
    name (returning every product in the family). Each product carries a runtime display name.
    """
    key = normalise_alias(model_or_family)
    rows = conn.execute(text(_SQL)).mappings().all()

    # Model matches, ranked: an exact alias match first, then sibling VARIANTS — products in the
    # same series (leading letters) that share the queried model number (so "MCH-16" also returns
    # the "MCH-13/16" electric/silent variants, not just the exact one). A query with no model
    # number (e.g. "CCDU") has no siblings and behaves as before.
    q_prefix, q_nums = _model_tokens(model_or_family)
    ranked: list[tuple[tuple, object]] = []
    for r in rows:
        if r["model_name"] and normalise_alias(r["model_name"]) == key:
            ranked.append(((0, 0, 0, r["product_id"]), r))  # exact match — the lead
            continue
        if q_prefix and q_nums:
            for field in (r["model_name"], r["variant"]):
                p, nums = _model_tokens(field)
                overlap = q_nums & nums
                if p == q_prefix and overlap:
                    # rank: more shared numbers first, then the more specific (fewer) model
                    ranked.append(((1, -len(overlap), len(nums), r["product_id"]), r))
                    break
    if ranked:
        ranked.sort(key=lambda t: t[0])
        seen_ids: set = set()
        products = []
        for _, r in ranked:
            if r["product_id"] not in seen_ids:
                seen_ids.add(r["product_id"])
                products.append(_as_product(r))
        matched_by = "model" if ranked[0][0][0] == 0 else "model_variant"
        return {"query": model_or_family, "matched_by": matched_by, "products": products}

    # Family resolution over the FULL registry (product-backed + capability-only families). Load
    # once and share between the exact tier and the token-coverage fallback.
    fams = conn.execute(text(_FAMILY_SQL)).mappings().all()

    # Alias-exact on family id or name — the most specific family signal, so it wins outright
    # (this also covers the capability-only process/gas families, which carry no product rows).
    exact = next(
        (
            f for f in fams
            if normalise_alias(f["family_id"]) == key
            or (f["family_name"] and normalise_alias(f["family_name"]) == key)
        ),
        None,
    )
    if exact is not None:
        return _family_result(conn, exact, rows, model_or_family, matched_by="family")

    # Token-coverage fallback (LLD-TOOL-03, station task): replaces raw longest-substring
    # containment, which matched "MCH-16" inside "Smart MCH-16" and ignored the dangling "Smart".
    # A candidate family's token bag = its name + its products' model_name/variant; a family is
    # confident only when it is the UNIQUE family whose covered query tokens are not a strict subset
    # of another's (Pareto frontier), so the candidate covering the distinguishing token wins.
    # 2–4 co-maximal candidates ⇒ ambiguous (the caller asks, listing display names); >4 ⇒ too
    # vague to disambiguate, treated as no match; 0 shared tokens ⇒ no match. Never fabricates.
    verdict = _token_coverage(model_or_family, rows, fams)
    if verdict["kind"] == "confident":
        fam = next(f for f in fams if f["family_id"] == verdict["family_id"])
        return _family_result(conn, fam, rows, model_or_family, matched_by="family_contains")
    if verdict["kind"] == "ambiguous":
        return {
            "query": model_or_family, "matched_by": "ambiguous",
            "products": [], "candidates": verdict["candidates"],
        }
    return {"query": model_or_family, "matched_by": None, "products": []}


def _family_result(
    conn: Connection, fam, rows, model_or_family: str, *, matched_by: str
) -> dict:
    """A resolved family → its product rows if it has any, else its published envelope.

    Product-backed families return ``matched_by`` verbatim (``family``/``family_contains``); a
    capability-only family (the process/gas lines: capability rows FK the family, no product rows)
    returns the ``family_envelope`` shape the renderer + citation derivation already understand.
    """
    prods = [r for r in rows if r["family_id"] == fam["family_id"]]
    if prods:
        return {
            "query": model_or_family, "matched_by": matched_by,
            "products": [_as_product(r) for r in prods],
        }
    return _envelope(conn, fam, model_or_family)


def _token_coverage(model_or_family: str, rows, fams) -> dict:
    """Score families by covered query tokens; return the confident / ambiguous / none verdict.

    ``{"kind": "confident", "family_id": …}`` when exactly one family sits on the Pareto frontier
    of covered query tokens (not strictly dominated by any other); ``{"kind": "ambiguous",
    "candidates": [display names]}`` for 2–4 co-maximal families; ``{"kind": "none"}`` for zero
    candidates or >4 (too vague to ask). The frontier is Exhibit C's rule made precise: a candidate
    leaving a query token that a competitor covers is strictly dominated and drops out.
    """
    q = _tokens(model_or_family)
    if not q:
        return {"kind": "none"}
    bag: dict[str, set[str]] = {}
    name: dict[str, str] = {}
    for f in fams:
        bag[f["family_id"]] = set(_tokens(f["family_name"]))
        name[f["family_id"]] = f["family_name"] or f["family_id"]
    for r in rows:
        b = bag.setdefault(r["family_id"], set())
        b |= _tokens(r["model_name"])
        b |= _tokens(r["variant"])
        name.setdefault(r["family_id"], r["family_name"] or r["family_id"])
    covered = {fid: (q & toks) for fid, toks in bag.items()}
    covered = {fid: c for fid, c in covered.items() if c}
    if not covered:
        return {"kind": "none"}
    # Pareto frontier: families whose covered token set is not a strict subset of another's.
    frontier = [
        fid for fid, c in covered.items()
        if not any(other != fid and c < covered[other] for other in covered)
    ]
    if len(frontier) == 1:
        return {"kind": "confident", "family_id": frontier[0]}
    if 2 <= len(frontier) <= 4:
        return {"kind": "ambiguous", "candidates": sorted(name[fid] for fid in frontier)}
    return {"kind": "none"}


def _envelope(conn: Connection, fam, model_or_family: str) -> dict:
    """Build the ``family_envelope`` result for an already-resolved family row (verbatim figures).

    A family with zero capability rows returns ``capabilities: []`` — never ``None`` — so the
    caller can still name and cite it.
    """
    capabilities = []
    for c in conn.execute(text(_CAP_SQL), {"fid": fam["family_id"]}).mappings():
        gases = [g["gas"] for g in conn.execute(text(_CAP_GAS_SQL), {"cid": c["cap_id"]}).mappings()]
        capabilities.append({**dict(c), "gases": gases})
    return {
        "query": model_or_family,
        "matched_by": "family_envelope",
        "products": [],
        "family": {
            "family_id": fam["family_id"],
            "family_name": fam["family_name"],
            "category": fam["category"],
            "division": fam["division"],
            "summary": fam["summary"],
            "source_doc_id": fam["source_doc_id"],
            "source_locator": fam["source_locator"],
        },
        "capabilities": capabilities,
    }
