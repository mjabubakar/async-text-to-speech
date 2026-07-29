"""Sentence-safe text chunking for TTS API submission.

Splits long text into chunks that stay under a character limit while
never breaking mid-sentence, so each chunk sounds natural when synthesized
and stitched back together.
"""

import nltk

_PUNKT_READY = False


def _ensure_punkt() -> None:
    """Download NLTK's sentence tokenizer data once, lazily."""
    global _PUNKT_READY
    if _PUNKT_READY:
        return
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)
    _PUNKT_READY = True


def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks under max_chars, breaking only at sentence boundaries.

    Paragraphs are kept together where possible. A single sentence longer
    than max_chars is returned as its own oversized chunk rather than being
    split mid-word — the caller is responsible for deciding how to handle
    that edge case (e.g. logging a warning, hard-splitting as a fallback).

    Args:
        text: The raw input text.
        max_chars: Maximum characters allowed per chunk.

    Returns:
        A list of text chunks, each a complete run of sentences.

    Raises:
        ValueError: If max_chars is not a positive integer.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    text = text.strip()
    if not text:
        return []

    _ensure_punkt()

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        sentences = nltk.sent_tokenize(paragraph)
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence

            if len(candidate) <= max_chars:
                current = candidate
                continue

            # candidate too big: flush what we have, start fresh with this sentence
            if current:
                chunks.append(current)
            current = sentence

            # the sentence itself exceeds max_chars — flush it alone as an
            # oversized chunk rather than silently merging it with neighbors
            if len(current) > max_chars:
                chunks.append(current)
                current = ""

    if current:
        chunks.append(current)

    return chunks


chunk_text("Hello world")
