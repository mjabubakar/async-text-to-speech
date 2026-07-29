import pytest

from app.chunking import chunk_text


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []


def test_whitespace_only_returns_empty_list():
    assert chunk_text("   \n\n   ") == []


def test_short_text_returns_single_chunk():
    text = "This is a short sentence. Here is another one."
    result = chunk_text(text, max_chars=4500)
    assert result == [text]


def test_splits_on_sentence_boundaries_not_mid_sentence():
    text = "First sentence here. Second sentence here. Third sentence here."
    # force a split after roughly one sentence
    result = chunk_text(text, max_chars=25)
    assert len(result) > 1
    for chunk in result:
        # every chunk should end with sentence-ending punctuation
        assert chunk.rstrip()[-1] in ".!?"


def test_no_chunk_exceeds_max_chars_when_sentences_fit():
    text = "Short one. Short two. Short three. Short four. Short five."
    max_chars = 20
    result = chunk_text(text, max_chars=max_chars)
    for chunk in result:
        assert len(chunk) <= max_chars


def test_oversized_single_sentence_is_its_own_chunk():
    long_sentence = "This is a single very long sentence " + ("word " * 50) + "end."
    result = chunk_text(long_sentence, max_chars=50)
    assert len(result) == 1
    assert result[0] == long_sentence.strip()


def test_paragraphs_are_respected():
    text = "Paragraph one sentence.\n\nParagraph two sentence."
    result = chunk_text(text, max_chars=4500)
    # both paragraphs fit comfortably in one chunk together
    assert len(result) == 1
    assert "Paragraph one" in result[0]
    assert "Paragraph two" in result[0]


def test_reconstructed_chunks_preserve_all_sentences():
    text = "Alpha sentence. Beta sentence. Gamma sentence. Delta sentence."
    result = chunk_text(text, max_chars=30)
    combined = " ".join(result)
    for word in ["Alpha", "Beta", "Gamma", "Delta"]:
        assert word in combined


def test_zero_max_chars_raises_value_error():
    with pytest.raises(ValueError):
        chunk_text("Some text.", max_chars=0)


def test_negative_max_chars_raises_value_error():
    with pytest.raises(ValueError):
        chunk_text("Some text.", max_chars=-10)


def test_abbreviations_do_not_cause_false_sentence_splits():
    text = "Dr. Smith went to the store. He bought milk."
    result = chunk_text(text, max_chars=4500)
    # nltk should treat "Dr. Smith" as one sentence unit, not split after "Dr."
    assert len(result) == 1
    assert "Dr. Smith went to the store." in result[0]
