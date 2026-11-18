"""Text from Adobe made safe to print in a terminal."""

import unicodedata
from typing import Any

# Characters that would move the cursor, start a new line or turn the text around in a terminal:
# controls, line and paragraph separators, and the marks that change the direction of text.
_UNSAFE_CATEGORIES = ("Cc", "Zl", "Zp")
_DIRECTION_MARKS = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def inert(text: Any) -> str:
    """The text with each character that could change how a terminal shows it as "?"."""
    return "".join(
        "?" if unicodedata.category(c) in _UNSAFE_CATEGORIES or c in _DIRECTION_MARKS else c
        for c in str(text)
    )
