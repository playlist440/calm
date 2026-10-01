"""Turning a name somebody typed into a key everything else can point at.

A name is for reading and a key is for pointing at, and conflating the two is
how integrations end up with switches that rename themselves. The rule here
is that **a key is derived from a name once and then never again**. Rename
the thing and the key stays; the entity keeps its id, the automation that
refers to it keeps working, and the stored settings still find their owner.

That rule is only worth anything if the key cannot be changed by accident,
which is why the name is asked for once at creation and is not offered again
afterwards. It is not a restriction for its own sake: an editable name with a
derived key means either the key drifts (and everything pointing at it
breaks) or it does not (and the key stops matching the name, which is worse
than not being able to edit it).
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

#: Letters that survive Unicode decomposition intact, because they are not a
#: base letter plus a mark — so stripping marks leaves them untouched and the
#: ASCII filter afterwards would drop them entirely. A Dutch household is
#: unlikely to need most of these; "ß" costs one line and saves a support
#: question.
TRANSLITERATIONS = {
    "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o",
    "đ": "d", "ð": "d", "þ": "th", "ł": "l",
}

#: Long enough for any name worth typing, short enough that the entity id it
#: ends up inside stays readable.
MAX_LENGTH = 40


def slugify(name: str) -> str:
    """The key for this name: lower case, ASCII, words joined by underscores.

    Accents are folded rather than dropped, so "Bureau" and "Buréau" do not
    become two different keys that look identical in a list. Anything that is
    not a letter or a digit becomes a separator, and runs of separators
    collapse — "werklicht  (keuken)" and "Werklicht - keuken" both land on
    ``werklicht_keuken``.

    Returns an empty string for a name with nothing usable in it, which the
    caller must treat as "ask again" rather than as a key.
    """
    folded = "".join(TRANSLITERATIONS.get(character, character)
                     for character in name.lower())
    decomposed = unicodedata.normalize("NFKD", folded)
    letters = [
        character if character.isascii() and character.isalnum() else "_"
        for character in decomposed
        if not unicodedata.combining(character)
    ]
    words = [word for word in "".join(letters).split("_") if word]
    return "_".join(words)[:MAX_LENGTH].strip("_")


def unique_key(name: str, taken: Iterable[str]) -> str:
    """A key for this name that none of ``taken`` already uses.

    Numbered rather than mangled: a second "Werklicht" becomes
    ``werklicht_2``, which is what a person would have written themselves.
    Returns an empty string when the name yields no key at all.
    """
    base = slugify(name)
    if not base:
        return ""
    already = set(taken)
    if base not in already:
        return base
    for number in range(2, 1000):
        suffix = "_%d" % number
        candidate = base[: MAX_LENGTH - len(suffix)].strip("_") + suffix
        if candidate not in already:
            return candidate
    raise ValueError("geen vrije sleutel meer voor %r" % name)
