import pytest

from ahd.text import inert

# The marks that set or change the direction of text: ALM, LRM, RLM, LRE to RLO, LRI to PDI.
MARKS = "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"


@pytest.mark.parametrize("mark", list(MARKS))
def test_every_direction_mark_becomes_a_question_mark(mark):
    assert inert(f"ab{mark}cd") == "ab?cd"


@pytest.mark.parametrize("text", ["\x1b[2J", "a\nb", "a\u2028b", "a\u2029b", "\x7f"])
def test_controls_and_line_breaks_become_question_marks(text):
    assert "?" in inert(text) and inert(text).isprintable()


def test_ordinary_text_stays_as_it_is():
    assert inert("Café 日本サイト evar1 [x]: 50%") == "Café 日本サイト evar1 [x]: 50%"
