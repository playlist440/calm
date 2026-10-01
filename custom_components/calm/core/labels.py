"""Turning entity ids into the names people gave their lamps.

A config flow renders a field's key when there is no translation for it, and
the keys for a per-lamp slider have to be entity ids because that is what
gets stored. The result is a screen that asks about
``light.hue_color_spot_1_2`` two lines below a picker calling the same lamp
"Spot C9" — one screen, two names, and it reads as a fault because it
behaves like one.

So the name becomes the key and is mapped back afterwards. It lives here
rather than in the flow for the dull reason that here it can be tested: the
part worth testing is what happens when two lamps are called the same thing,
and that has nothing to do with Home Assistant.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional


def label_lamps(
    entity_ids: Iterable[str], name_of: Callable[[str], Optional[str]]
) -> Dict[str, str]:
    """Map a label per lamp back to its entity id, in the given order.

    Two lamps with the same name both get their entity id appended — not
    just the second one. Leaving the first as the bare name would quietly
    suggest it is *the* "Spot", and picking the wrong one of two identically
    named lamps is a mistake nobody would ever think to look for.
    """
    labels: Dict[str, str] = {}
    claimed: Dict[str, str] = {}

    for entity_id in entity_ids:
        name = (name_of(entity_id) or "").strip() or entity_id
        if name not in claimed:
            claimed[name] = entity_id
            labels[name] = entity_id
            continue

        first = claimed[name]
        labels.pop(name, None)
        labels["%s (%s)" % (name, first)] = first
        labels["%s (%s)" % (name, entity_id)] = entity_id

    return labels
