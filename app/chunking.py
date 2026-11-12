"""Sentence-safe text chunking for TTS API submission.

Splits long text into chunks that never exceed a character limit and, wherever
possible, break only at sentence boundaries.
"""

import re

import nltk

_PUNKT_READY = False
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


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


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Split an oversized sentence on whitespace; cut a single word only if it
    alone exceeds max_chars."""
    parts: list[str] = []
    current = ""
    for word in sentence.split():
        while len(word) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks of at most max_chars.

    Sentences (NLTK Punkt, English) are packed greedily. A paragraph break between
    two sentences in the same chunk is kept as a blank line. A sentence longer than
    max_chars is split on whitespace (see _hard_split), so every returned chunk is
    guaranteed to be <= max_chars.

    Raises:
        ValueError: If max_chars is not a positive integer.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    _ensure_punkt()

    chunks: list[str] = []
    current = ""

    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        separator = "\n\n"  # only before the first piece of a new paragraph
        for sentence in nltk.sent_tokenize(paragraph):
            pieces = (
                _hard_split(sentence, max_chars)
                if len(sentence) > max_chars
                else [sentence]
            )
            for piece in pieces:
                if not current:
                    current = piece
                elif len(current) + len(separator) + len(piece) <= max_chars:
                    current = f"{current}{separator}{piece}"
                else:
                    chunks.append(current)
                    current = piece
                separator = " "

    if current:
        chunks.append(current)
    return chunks
