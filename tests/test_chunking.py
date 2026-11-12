import pytest

from app.chunking import chunk_text


def normalize(s: str) -> str:
    return " ".join(s.split())


def test_empty_and_whitespace_return_empty_list():
    assert chunk_text("") == []
    assert chunk_text("   \n\n   ") == []


@pytest.mark.parametrize("bad", [0, -10])
def test_non_positive_max_chars_raises(bad):
    with pytest.raises(ValueError):
        chunk_text("Some text.", max_chars=bad)


def test_short_text_is_a_single_unchanged_chunk():
    text = "This is a short sentence. Here is another one."
    assert chunk_text(text, max_chars=4500) == [text]


def test_splits_exactly_on_sentence_boundaries():
    text = "First sentence here. Second sentence here. Third sentence here."
    assert chunk_text(text, max_chars=25) == [
        "First sentence here.",
        "Second sentence here.",
        "Third sentence here.",
    ]


def test_every_chunk_within_limit_and_content_preserved():
    text = "Short one. Short two. Short three. Short four. Short five."
    chunks = chunk_text(text, max_chars=20)
    assert all(len(c) <= 20 for c in chunks)
    assert normalize(" ".join(chunks)) == normalize(text)


def test_paragraph_break_is_preserved_inside_a_chunk():
    assert chunk_text("Paragraph one.\n\nParagraph two.", max_chars=4500) == [
        "Paragraph one.\n\nParagraph two."
    ]


def test_crlf_paragraph_breaks_are_normalized():
    assert chunk_text("Para one.\r\n\r\nPara two.", max_chars=4500) == [
        "Para one.\n\nPara two."
    ]


def test_oversized_sentence_is_split_and_every_chunk_fits():
    sentence = "This is a single very long sentence " + ("word " * 50) + "end."
    chunks = chunk_text(sentence, max_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    assert normalize(" ".join(chunks)) == normalize(sentence)


def test_single_word_longer_than_limit_is_cut():
    chunks = chunk_text("x" * 105, max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert "".join(chunks) == "x" * 105


def test_abbreviation_does_not_cause_false_split():
    text = "Dr. Smith went to the store. He bought milk."
    assert chunk_text(text, max_chars=4500) == [text]
