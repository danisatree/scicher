"""Scicher's themes — both built from the same 8-color "Dark Azure" palette
(00638E/FFFFFF/004A6B/8CB9CC/000000/141414/2B2B2B/BFD8E3). Neither theme has a
distinct green or red in the source palette, so `success`/`warning`/`error`
intentionally reuse other palette colors rather than reaching for off-palette
ones."""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#00638E",
    secondary="#8CB9CC",
    accent="#BFD8E3",
    warning="#00638E",
    error="#004A6B",
    success="#BFD8E3",
    foreground="#FFFFFF",
    background="#000000",
    surface="#141414",
    panel="#2B2B2B",
    boost="#2B2B2B",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#00638E",
    secondary="#004A6B",
    accent="#2B2B2B",
    warning="#004A6B",
    error="#2B2B2B",
    success="#00638E",
    foreground="#141414",
    background="#BFD8E3",
    surface="#FFFFFF",
    panel="#FFFFFF",
    boost="#FFFFFF",
    dark=False,
)
