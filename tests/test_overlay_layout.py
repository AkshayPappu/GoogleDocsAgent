import unittest

from gdocs_agent.overlay_layout import (
    BAR_H,
    BUG,
    COLLAPSED,
    GAP,
    NOTICE_H,
    PAD,
    UPDATES_H,
    clamp_bug,
    compute,
    default_bug,
    stylesheet,
)

SCREEN = [(0, 0, 1920, 1080)]


class OverlayLayoutTests(unittest.TestCase):
    def test_stylesheet_uses_the_same_sizes(self):
        css = stylesheet()
        self.assertIn(f"--bug: {BUG}px", css)
        self.assertIn(f"--bar-h: {BAR_H}px", css)
        self.assertIn(f"--updates-h: {UPDATES_H}px", css)
        self.assertIn(f"--notice: {NOTICE_H}px", css)

    def test_default_sits_in_the_lower_right(self):
        bug_x, bug_y = default_bug(SCREEN)
        self.assertGreater(bug_x, 1500)
        self.assertGreater(bug_y, 900)
        self.assertLessEqual(bug_x + BUG, 1920)
        self.assertLessEqual(bug_y + BUG, 1080)

    def test_clamp_pulls_the_button_back_on_screen(self):
        self.assertEqual(clamp_bug(5000, -40, SCREEN), (1920 - BUG, 0))

    def test_button_stays_put_as_the_panel_opens(self):
        for mode in ("bug", "bar", "updates", "menu"):
            for notice in (False, True):
                for point in ((30, 40), (30, 1000), (1600, 20), (800, 600)):
                    layout, bug_x, bug_y = compute(*point, mode, SCREEN, notice)
                    self.assertEqual((bug_x, bug_y), point)
                    self.assertEqual(layout.x + layout.bug_left, bug_x)
                    self.assertEqual(layout.y + layout.bug_top, bug_y)
                    self.assertLessEqual(layout.bug_left + BUG, layout.width)
                    self.assertLessEqual(layout.bug_top + BUG, layout.height)
                    if not (layout.show_panel or layout.show_menu):
                        self.assertEqual((layout.width, layout.height), (COLLAPSED, COLLAPSED))
                        continue
                    self.assertGreaterEqual(layout.panel_left, 0)
                    self.assertGreaterEqual(layout.panel_top, 0)
                    self.assertLessEqual(layout.panel_left + layout.panel_width, layout.width)
                    self.assertLessEqual(layout.panel_top + layout.panel_height, layout.height)
                    if layout.anchor == "right":
                        gap = layout.bug_left - (layout.panel_left + layout.panel_width)
                    else:
                        gap = layout.panel_left - (layout.bug_left + BUG)
                    self.assertEqual(gap, GAP)

    def test_bar_grows_inward_from_the_edges(self):
        opened, _, _ = compute(1400, 800, "bar", SCREEN, False)
        self.assertEqual(opened.anchor, "right")
        self.assertEqual(opened.vertical, "bottom")
        self.assertLess(opened.x, 1400)

        flipped, _, _ = compute(12, 800, "bar", SCREEN, False)
        self.assertEqual(flipped.anchor, "left")
        self.assertEqual(flipped.x + flipped.bug_left, 12)

        downward, _, _ = compute(800, 8, "updates", SCREEN, False)
        self.assertEqual(downward.vertical, "top")
        self.assertEqual(downward.y + downward.bug_top, 8)

    def test_connect_notice_makes_the_bar_taller(self):
        plain, _, _ = compute(800, 800, "bar", SCREEN, False)
        notice, _, _ = compute(800, 800, "bar", SCREEN, True)
        self.assertEqual(notice.panel_height, plain.panel_height + NOTICE_H)
        self.assertGreater(notice.height, plain.height)

    def test_updates_are_taller_than_the_bar(self):
        bar, _, _ = compute(800, 800, "bar", SCREEN, False)
        updates, _, _ = compute(800, 800, "updates", SCREEN, False)
        self.assertGreater(updates.height, bar.height)
        self.assertEqual(updates.panel_height, UPDATES_H)

    def test_left_monitor_still_opens_beside_the_button(self):
        areas = [(-1920, 0, 1920, 1080), (0, 0, 1920, 1080)]
        layout, bug_x, _ = compute(-400, 500, "bar", areas, False)
        self.assertEqual(bug_x, -400)
        self.assertEqual(layout.anchor, "right")
        self.assertEqual(layout.x + layout.bug_left, -400)
