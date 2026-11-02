"""Scicher's themes — both built from the same "Starfield" 6-color palette
(D5DAD7 Silver Greene / D3A54F Golden Freestar / D35F3F Alien Aurora /
B72B3C Crimson Fleet / 334A73 Blue Constellation / 1F2C41 Deep Vanguard).
No green in the source palette, so `success` reuses another color rather
than reaching for an off-palette one; `warning`/`error` map naturally onto
the gold/crimson tones, which is a rare case where the palette's own
colors already carry the right semantics. The dark theme's panel/boost
(#111824) is a darker derived shade of Deep Vanguard rather than one of
the 6 named colors — panels need to read as darker than the background
there, and nothing in the source palette was dark enough for that."""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#D3A54F",
    secondary="#D35F3F",
    accent="#B72B3C",
    warning="#D3A54F",
    error="#B72B3C",
    success="#D5DAD7",
    foreground="#D5DAD7",
    background="#1F2C41",
    surface="#1F2C41",
    panel="#111824",
    boost="#111824",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#334A73",
    secondary="#1F2C41",
    accent="#B72B3C",
    warning="#D3A54F",
    error="#B72B3C",
    success="#D35F3F",
    foreground="#1F2C41",
    background="#D5DAD7",
    surface="#D5DAD7",
    panel="#D5DAD7",
    boost="#D5DAD7",
    dark=False,
)
