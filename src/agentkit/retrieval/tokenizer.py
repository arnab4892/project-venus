"""Token counting for chunking + embedding (LLD-RET-02, the "LLD 1.1 rule").

The embedder MUST count tokens with the *same* tokenizer the embedding model uses
(bge-m3), set the provider context window (`num_ctx`) explicitly, and raise a loud
:class:`ChunkTooLargeError` rather than let the provider silently truncate. This module
owns that count.

Counting is a seam — a plain ``Callable[[str], int]`` (:data:`TokenCounter`) — so tests
inject a deterministic fake (e.g. word count) and never load the 16 MB bge-m3 tokenizer.
The production counter, :func:`bge_m3_counter`, loads the real tokenizer from a configurable
id/path (``embed_tokenizer`` / ``embed_tokenizer_path``) resolved from the local Hugging Face
cache — no per-request network, honouring PRD-N-002 self-hosting.

Framework-generic — no client strings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from agentkit.config import Settings, get_settings

# A token-counting seam: text -> token count for the embedding model's tokenizer.
TokenCounter = Callable[[str], int]


class ChunkTooLargeError(RuntimeError):
    """A chunk's token count reached the embed limit — never silently truncate.

    Raised at chunk time (LLD-RET-02) so an over-long chunk fails the run loudly
    instead of being cut short by the provider mid-content.
    """

    def __init__(self, *, locator: str, token_count: int, embed_limit: int) -> None:
        self.locator = locator
        self.token_count = token_count
        self.embed_limit = embed_limit
        super().__init__(
            f"chunk at {locator!r} is {token_count} tokens, at/over the embed limit "
            f"of {embed_limit} — split it or raise the limit; refusing to truncate."
        )


@lru_cache(maxsize=4)
def _load_tokenizer(spec: str):  # pragma: no cover - exercised only with the real asset
    """Load a Hugging Face fast tokenizer from a local file, dir, or repo id.

    ``spec`` is a path to a ``tokenizer.json`` (or a dir containing one) for an
    air-gapped deploy, else a repo id resolved from the local HF cache.
    """
    from pathlib import Path

    from tokenizers import Tokenizer

    path = Path(spec)
    if path.is_file():
        return Tokenizer.from_file(str(path))
    if path.is_dir() and (path / "tokenizer.json").is_file():
        return Tokenizer.from_file(str(path / "tokenizer.json"))
    # A repo id — resolved from the local HF cache (populated once, offline thereafter).
    return Tokenizer.from_pretrained(spec)


def bge_m3_counter(settings: Settings | None = None) -> TokenCounter:
    """Return a :data:`TokenCounter` backed by the real embedding tokenizer.

    Resolution order: ``embed_tokenizer_path`` (local file/dir, for air-gapped
    installs) then ``embed_tokenizer`` (repo id via the HF cache). The tokenizer is
    loaded once and reused; counting excludes special tokens so the number reflects
    content length.
    """
    settings = settings or get_settings()
    spec = settings.embed_tokenizer_path or settings.embed_tokenizer
    tokenizer = _load_tokenizer(spec)

    def count(text: str) -> int:  # pragma: no cover - thin wrapper over the C tokenizer
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return count
