"""Response-language instruction selection (LLD-RT-07).

``respond_in`` injects the reply-language instruction into the agent's compose prompt. Each of the
three languages gets a **firm, self-sufficient last-position instruction of equal strength** — a
bare label ("Respond in English.") let exemplar imitation override it, and a live regression saw an
English turn recite the Hinglish exemplar. English forbids Hindi/Hinglish; Hinglish pins the
romanised register (no Devanagari, no pure-English drift); Hindi carries a two-line
written-Devanagari register reminder. All three close with the same clause — examples show structure
only, the reply language comes only from this instruction. This file covers the selection only — the
Hindi register itself lives in ``_persona.md`` and is asserted end-to-end by the ``script_purity``
goldens.
"""

from __future__ import annotations

from agentkit.runtime.language import respond_in

# The shared closing clause every language carries, so exemplar imitation can never pick the reply
# language — the regression that motivated the symmetric strengthening (ADDITIONAL SCOPE fix 1).
_SHARED_TAIL = "the reply language comes ONLY from this instruction."


def test_english_forbids_hindi_and_hinglish_firmly():
    out = respond_in("en")
    assert out != "Respond in English."  # no longer a bare label
    lowered = out.lower()
    assert "english only" in lowered
    assert "hindi" in lowered and "hinglish" in lowered  # explicitly forbids both
    assert _SHARED_TAIL in out


def test_none_and_unknown_default_to_english():
    assert respond_in(None) == respond_in("en")
    assert respond_in("fr") == respond_in("en")


def test_hinglish_pins_the_romanised_register_firmly():
    out = respond_in("hinglish")
    assert out != "Respond in Hinglish (romanised Hindi/English)."  # no longer a bare label
    lowered = out.lower()
    assert "hinglish" in lowered
    assert "no devanagari" in lowered  # stays romanised
    assert "pure english" in lowered  # and does not drift the other way
    assert _SHARED_TAIL in out


def test_hindi_is_a_two_line_register_reminder():
    out = respond_in("hi")
    # Two lines, not the bare "Respond in Hindi." — recency: the written-Devanagari rule is the
    # last thing the compose model reads.
    assert out != "Respond in Hindi."
    assert out.count("\n") == 1
    lowered = out.lower()
    assert "devanagari" in lowered
    assert "bold" in lowered  # names in bold
    assert "unit" in lowered and "standard code" in lowered  # the two other Latin exceptions
    assert "ascii" in lowered  # figures stay ASCII digits
    assert _SHARED_TAIL in out  # same closing clause as en/hinglish — equal strength


def test_all_three_languages_share_the_language_only_tail():
    # Symmetry is the fix: no language may leave the reply-language choice to exemplar imitation.
    for lang in ("en", "hi", "hinglish"):
        assert _SHARED_TAIL in respond_in(lang)


def test_language_selection_is_case_insensitive():
    assert respond_in("HI") == respond_in("hi")
    assert respond_in("EN") == respond_in("en")
    assert respond_in("Hinglish") == respond_in("hinglish")
