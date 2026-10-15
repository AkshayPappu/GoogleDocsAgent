"""Size and place the floating chat so the button stays under the pointer."""

from __future__ import annotations

from dataclasses import dataclass

BUG = 48
PAD = 10
GAP = 8
BAR_W = 312
BAR_H = 52
UPDATES_H = 150
NOTICE_H = 44
MENU_W = 180
MENU_H = 84
COLLAPSED = BUG + PAD * 2
_MARGIN = 28
_MODES = {"bug", "bar", "updates", "menu"}

Area = tuple[int, int, int, int]


@dataclass(frozen=True)
class Layout:
    x: int
    y: int
    width: int
    height: int
    anchor: str
    vertical: str
    bug_left: int
    bug_top: int
    panel_left: int
    panel_top: int
    panel_width: int
    panel_height: int
    show_panel: bool
    show_menu: bool


def stylesheet() -> str:
    """CSS variables shared with the overlay page."""
    return f"""
:root {{
  --bug: {BUG}px;
  --pad: {PAD}px;
  --gap: {GAP}px;
  --bar-w: {BAR_W}px;
  --bar-h: {BAR_H}px;
  --updates-h: {UPDATES_H}px;
  --notice: {NOTICE_H}px;
  --menu-w: {MENU_W}px;
  --menu-h: {MENU_H}px;
}}
""".strip()


def default_bug(areas: list[Area]) -> tuple[int, int]:
    """Lower-right corner of the primary work area, inset from the edges."""
    origin_x, origin_y, width, height = _primary(areas)
    return clamp_bug(
        origin_x + width - BUG - _MARGIN,
        origin_y + height - BUG - _MARGIN,
        areas,
    )


def clamp_bug(bug_x: float, bug_y: float, areas: list[Area]) -> tuple[int, int]:
    """Keep the whole button inside one work area."""
    bug_x = int(round(bug_x))
    bug_y = int(round(bug_y))
    if not areas:
        return bug_x, bug_y
    if any(_contains(bug_x, bug_y, area) for area in areas):
        return bug_x, bug_y
    best: tuple[int, int] | None = None
    best_distance: int | None = None
    for origin_x, origin_y, width, height in areas:
        fitted_x = _fit(bug_x, origin_x, width)
        fitted_y = _fit(bug_y, origin_y, height)
        distance = (fitted_x - bug_x) ** 2 + (fitted_y - bug_y) ** 2
        if best_distance is None or distance < best_distance:
            best = (fitted_x, fitted_y)
            best_distance = distance
    return best or (bug_x, bug_y)


def compute(
    bug_x: float,
    bug_y: float,
    mode: str,
    areas: list[Area],
    notice: bool,
) -> tuple[Layout, int, int]:
    """Return the window rectangle and the clamped button position.

    The button's screen position does not change when the panel opens.
    The panel grows beside it, inward from the nearest screen edge.
    """
    if mode not in _MODES:
        mode = "bar"
    bug_x, bug_y = clamp_bug(bug_x, bug_y, areas)
    if mode == "bug":
        return (
            Layout(
                x=bug_x - PAD,
                y=bug_y - PAD,
                width=COLLAPSED,
                height=COLLAPSED,
                anchor="right",
                vertical="bottom",
                bug_left=PAD,
                bug_top=PAD,
                panel_left=0,
                panel_top=0,
                panel_width=0,
                panel_height=0,
                show_panel=False,
                show_menu=False,
            ),
            bug_x,
            bug_y,
        )

    panel_width, panel_height = _panel_size(mode, notice)
    origin_x, origin_y, width, height = _area_for(bug_x, bug_y, areas)
    room_left = bug_x - origin_x
    room_right = origin_x + width - (bug_x + BUG)
    needed = panel_width + GAP
    anchor = "right" if room_left >= needed or room_left >= room_right else "left"

    window_width = PAD + panel_width + GAP + BUG + PAD
    window_height = PAD + max(BUG, panel_height) + PAD
    extra = max(0, panel_height - BUG) + PAD
    room_above = bug_y - origin_y
    room_below = origin_y + height - (bug_y + BUG)
    vertical = "bottom" if room_above >= extra or room_above >= room_below else "top"

    if anchor == "right":
        window_x = bug_x + BUG + PAD - window_width
        bug_left = window_width - PAD - BUG
        panel_left = PAD
    else:
        window_x = bug_x - PAD
        bug_left = PAD
        panel_left = PAD + BUG + GAP

    if vertical == "bottom":
        window_y = bug_y + BUG + PAD - window_height
        bug_top = window_height - PAD - BUG
        panel_top = window_height - PAD - panel_height
    else:
        window_y = bug_y - PAD
        bug_top = PAD
        panel_top = PAD

    return (
        Layout(
            x=window_x,
            y=window_y,
            width=window_width,
            height=window_height,
            anchor=anchor,
            vertical=vertical,
            bug_left=bug_left,
            bug_top=bug_top,
            panel_left=panel_left,
            panel_top=panel_top,
            panel_width=panel_width,
            panel_height=panel_height,
            show_panel=mode in {"bar", "updates"},
            show_menu=mode == "menu",
        ),
        bug_x,
        bug_y,
    )


def _panel_size(mode: str, notice: bool) -> tuple[int, int]:
    if mode == "menu":
        return MENU_W, MENU_H
    height = UPDATES_H if mode == "updates" else BAR_H
    if notice:
        height += NOTICE_H
    return BAR_W, height


def _primary(areas: list[Area]) -> Area:
    for origin_x, origin_y, width, height in areas:
        if origin_x <= 0 <= origin_x + width and origin_y <= 0 <= origin_y + height:
            return (origin_x, origin_y, width, height)
    return areas[0] if areas else (0, 0, 1280, 800)


def _area_for(bug_x: int, bug_y: int, areas: list[Area]) -> Area:
    for area in areas:
        if _contains(bug_x, bug_y, area):
            return area
    return _primary(areas)


def _contains(bug_x: int, bug_y: int, area: Area) -> bool:
    origin_x, origin_y, width, height = area
    if width < BUG or height < BUG:
        return False
    return (
        origin_x <= bug_x
        and bug_x + BUG <= origin_x + width
        and origin_y <= bug_y
        and bug_y + BUG <= origin_y + height
    )


def _fit(value: int, start: int, size: int) -> int:
    if size <= BUG:
        return start
    return min(max(value, start), start + size - BUG)
