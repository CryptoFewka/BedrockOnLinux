"""What the activity log actually shows the player.

The log is the first thing anyone is asked to paste into a bug report, so
what is in it has to be what was logged. QTextEdit.append() picks between
rich and plain text with Qt::mightBeRichText(), which means escaping a
string without also making it look like markup gets the escaping rendered
instead of applied.
"""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from bol.log import _LEVELS
from tests.guiharness import headless_window, qt_app


class ActivityLogRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def _rendered(self, *lines, **settings):
        with headless_window(**settings) as window:
            for line in lines:
                window._on_log_line(line)
            return window.log_view.toPlainText()

    def test_an_arrow_prefix_is_not_rendered_as_an_entity(self):
        # "-> " is what every download step logs, and it carries no level, so
        # it takes the plain branch. It used to arrive as "-&gt;".
        text = self._rendered("-> downloading 1.21.130.7")
        self.assertIn("-> downloading 1.21.130.7", text)
        self.assertNotIn("&gt;", text)

    def test_a_step_prefix_survives_unchanged(self):
        self.assertIn("== preparing engine",
                      self._rendered("== preparing engine"))

    def test_angle_brackets_in_an_error_are_shown_as_written(self):
        # urllib errors carry their reason in angle brackets, and that is
        # exactly the line a bug report is built from.
        text = self._rendered("xx HTTP error <urlopen error timed out>")
        self.assertIn("<urlopen error timed out>", text)
        self.assertNotIn("&lt;", text)
        self.assertNotIn("&gt;", text)

    def test_an_ampersand_in_a_path_is_shown_as_written(self):
        text = self._rendered("-> /home/p/Games & Saves/world")
        self.assertIn("Games & Saves", text)
        self.assertNotIn("&amp;", text)

    def test_markup_in_a_log_line_is_never_interpreted(self):
        # The other half of the bargain: escaping still has to happen, or a
        # game path could style the log or drop text from it.
        text = self._rendered("-> <b>not bold</b>")
        self.assertIn("<b>not bold</b>", text)

    def test_a_levelled_line_keeps_its_label_and_message(self):
        text = self._rendered("xx launch failed")
        self.assertIn(_LEVELS["xx"][0].strip(), text)
        self.assertIn("launch failed", text)

    def test_the_view_stays_on_the_newest_line(self):
        with headless_window() as window:
            window.log_drawer.show()
            window.log_view.resize(400, 60)
            for index in range(200):
                window._on_log_line(f"-> line {index}")
            bar = window.log_view.verticalScrollBar()
            self.assertEqual(bar.value(), bar.maximum(),
                             "the activity log scrolled away from the newest "
                             "line, which is the one anyone is watching")


class StatusColourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_a_failure_paints_the_status_red(self):
        with headless_window() as window:
            window.ui_state["busy"] = True
            window._on_log_line("xx it broke")
            self.assertIn(window.theme.red,
                          window.status_label.styleSheet())

    def test_the_next_launch_paints_it_back(self):
        # One failed launch used to leave every later "Preparing…" and
        # "Downloading…" red, because only setText was called after that.
        with headless_window() as window:
            window.ui_state["busy"] = True
            window._on_log_line("xx it broke")
            window.set_progress(1, 2)
            self.assertNotIn(window.theme.red,
                             window.status_label.styleSheet())

    def test_a_recovered_status_is_not_left_red_either(self):
        with headless_window() as window:
            window.ui_state["busy"] = True
            window._on_log_line("xx it broke")
            with mock.patch.object(window, "_friendly",
                                   return_value=("Minecraft is running", True)):
                window._on_log_line("== minecraft is running")
            self.assertNotIn(window.theme.red,
                             window.status_label.styleSheet())


if __name__ == "__main__":
    unittest.main()
