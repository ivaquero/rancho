"""The languages the field simulator speaks, and one way to name things in them.

Names reach the UI from many places — crops, channels, growth stages, climate
bands, weather series and scenario presets.  Each used to carry a ``label`` /
``label_en`` / ``label_es`` triplet, so adding a fourth language meant editing
that triplet on every model *and* every wire emitter that hand-picked the
variants — the classic open/closed violation, where a closed list of fields grows
with the open set of human languages.

A name is now a single mapping keyed by language code.  The backend builds the
whole map once and ships it verbatim; the client picks the entry for the active
language and falls back along the same chain the server used to fill the map.
Adding a language is a new key in the maps plus a locale dictionary on the
frontend — never a new field somewhere in Python.
"""

from __future__ import annotations

from typing import Final

#: The base language: the value every ``.label`` property returns and every map
#: is guaranteed to carry.  Chinese is the authoring language of the models.
BASE_LANG: Final = "zh"

#: Languages a bundled name is always filled in.  Kept beside :func:`localize` so
#: the two stay honest about which variants the wire promises to carry.
LANGUAGES: Final = ("zh", "en", "es")


def localize(base: str, /, **variants: str | None) -> dict[str, str]:
    """Build a complete language map from a base name and optional variants.

    Walking :data:`LANGUAGES` in order, each language takes the variant supplied
    for it or, when missing, the previously resolved value — so an untranslated
    Spanish name still reads English, and everything still reads the base when
    nothing else is given.  The result always carries every key in
    :data:`LANGUAGES`, and adding a language means adding it to that tuple,
    never editing this function.
    """
    resolved: dict[str, str] = {}
    previous = base
    for language in LANGUAGES:
        previous = variants.get(language) or previous
        resolved[language] = previous
    return resolved


def labels_from(
    table: dict[str, dict[str, str]],
    key: str,
    default: str | None = None,
) -> dict[str, str]:
    """Look *key* up in a nested language table, falling back to the raw name.

    For display tables (stages, climates) keyed by a machine identifier: an
    unknown identifier still needs a label on the wire, so it becomes a map whose
    every language is the bare key — visible on screen rather than blank.
    """
    found = table.get(key)
    if found is not None:
        return dict(found)
    return localize(default if default is not None else key)
