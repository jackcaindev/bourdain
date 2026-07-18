"""Normalization helpers for destination-specific city research."""

from __future__ import annotations

import re


def normalize_city_slug(destination: str) -> str:
    """Return a lowercase, punctuation-free slug for a destination name."""

    without_punctuation = "".join(
        character
        for character in destination.lower()
        if character.isalnum() or character.isspace()
    )
    return re.sub(r"\s+", "-", without_punctuation.strip())
