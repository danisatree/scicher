"""Scicher's themes — a clean, academic/lab-notebook light theme, and a vivid
purple/magenta dark theme. The dark palette's 6 colors (180B41/3C2E7A/DDDDFE/
93093D/FF6798/E80E49) are reused across more than 11 Theme roles by design —
there's no green in it, so `success` intentionally uses the light lavender
(DDDDFE) rather than an off-palette color."""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#FF6798",
    secondary="#DDDDFE",
    accent="#E80E49",
    warning="#E80E49",
    error="#93093D",
    success="#DDDDFE",
    foreground="#DDDDFE",
    background="#180B41",
    surface="#180B41",
    panel="#3C2E7A",
    boost="#3C2E7A",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#2f5d7c",
    secondary="#4d6b85",
    accent="#a8762c",
    warning="#b5861f",
    error="#a33f3f",
    success="#3f7d52",
    foreground="#1e232b",
    background="#f7f5f0",
    surface="#ffffff",
    panel="#ececE5",
    boost="#e2ded4",
    dark=False,
)
