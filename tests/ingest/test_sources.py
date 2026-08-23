"""Sources loading + deterministic doc-id derivation (LLD-ING-01/05)."""

from __future__ import annotations

from agentkit.ingest.sources import bootstrap_rc_id, doc_id_for, load_sources


def test_bootstrap_rc_id_is_per_client():
    assert bootstrap_rc_id("jyotech") == "rc.jyotech.bootstrap"


def test_doc_id_is_stable_and_generic():
    assert doc_id_for("https://www.jyotech.com/about.html") == "doc.about"
    assert doc_id_for("https://www.jyotech.com/index.php") == "doc.index"
    # percent-encoded and punctuated names slugify deterministically
    assert (
        doc_id_for("https://www.jyotech.com/pdf/Jyotech%20Catalog%20-%20PROCESS.pdf")
        == "doc.jyotech_catalog_process"
    )
    # the raw-space and %20 forms map to the same id
    assert doc_id_for("https://www.jyotech.com/portable%20Compressor.html") == doc_id_for(
        "https://www.jyotech.com/portable Compressor.html"
    )


def test_load_real_sources_yaml():
    src = load_sources("jyotech")
    assert src.client == "jyotech"
    assert src.start_url.endswith("index.php")
    assert len(src.pages) == 27
    assert len(src.pdfs) == 2
    # three template/duplicate pages excluded; active set = 24
    assert any("achievement.html" in u for u in src.exclude)
    assert any("fire-protection-clothing.html" in u for u in src.exclude)
    assert any("catalogue1.html" in u for u in src.exclude)
    assert len(src.active_pages()) == 24
    # per-client cleaner value lists are loaded
    assert "Image" in src.drop_alt_text
    assert "Read More" in src.drop_link_text
    # the derived chrome selectors are present
    assert "nav" in src.strip_selectors
    assert any(s.startswith(".container-fluid") for s in src.strip_selectors)
    # PDFs stored percent-encoded (literal space + &)
    assert any("%26S.pdf" in u for u in src.pdfs)
