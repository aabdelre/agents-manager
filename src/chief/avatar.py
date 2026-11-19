"""A hand-drawn chief, rendered with half blocks for crisp terminal pixels."""

import random

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

# Two square pixels per terminal cell. Keep the portrait at 20 × 16 so it fits
# beside its wordmark without adding height to the command desk.
PIXELS = (
    "       hhhhhh       ",
    "     hhHHHHHHhh     ",
    "    hHHAAAAHHHHh    ",
    "    hAAHHHHHHHHh    ",
    "    hHsssssssshH    ",
    "   tthsssssssshht   ",
    "   tTskkksskkksTt   ",
    "   tTskwksSkwksTt   ",
    "   tTskkkSSkkksTt   ",
    "   tt sssSSsssttt   ",
    "      sswwssstmm    ",
    "       SssssS       ",
    "     jjjWssWjjj     ",
    "   jjJjjWmmWjjJjj   ",
    "  jJJJjJjmmjJjJGJj  ",
    "  jJJJjJJmmJJjJJJj  ",
)

PALETTE = {
    " ": "#172335",  # backdrop
    "h": "#624e40",  # swept hair, shaded edge
    "H": "#b58b56",
    "A": "#edc585",
    "s": "#f2d4ac",  # face
    "S": "#cf9e77",
    "k": "#263a50",  # glasses
    "w": "#fff0d6",
    "t": "#365e68",  # headset
    "T": "#7de0bb",
    "m": "#7de0bb",  # microphone and tie
    "j": "#263c58",  # tailored jacket
    "J": "#456487",
    "W": "#e3eaf1",  # collar
    "G": "#edc585",  # lapel pin
}


def chief_portrait(*, eyes_closed=False) -> Text:
    pixels = list(PIXELS)
    if eyes_closed:
        pixels[7] = "   tTskkksSkkksTt   "
    portrait = Text(no_wrap=True)
    captions = {
        2: ("THE CHIEF", "bold #f3d6a6"),
        3: ("────────────", "#36516a"),
        4: ("Command desk", "#a9bad1"),
    }
    for line, y in enumerate(range(0, len(pixels), 2)):
        if line:
            portrait.append("\n")
        for top, bottom in zip(pixels[y], pixels[y + 1], strict=True):
            portrait.append("▀", style=Style(color=PALETTE[top], bgcolor=PALETTE[bottom]))
        caption, style = captions.get(line, ("", ""))
        portrait.append("  " + caption.ljust(12), style=style)
    return portrait


class ChiefAvatar(Static):
    """Blink briefly, then choose a fresh random pause before the next blink."""

    def __init__(self):
        self.open_eyes = chief_portrait()
        self.closed_eyes = chief_portrait(eyes_closed=True)
        super().__init__(self.open_eyes, id="avatar")

    def on_mount(self):
        self.schedule_blink()

    def schedule_blink(self):
        self.set_timer(random.uniform(3.0, 8.0), self.blink)

    def blink(self):
        self.update(self.closed_eyes, layout=False)
        self.set_timer(random.uniform(0.12, 0.18), self.reopen_eyes)

    def reopen_eyes(self):
        self.update(self.open_eyes, layout=False)
        self.schedule_blink()
