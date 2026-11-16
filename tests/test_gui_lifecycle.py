"""The launcher window's lifetime, and the process's lifetime behind it.

These two are deliberately not the same thing. `setQuitOnLastWindowClosed`
is off so that "close the launcher when Minecraft starts" can take the window
away while the launch thread keeps supervising the game -- a session nobody
watches out leaves the GPU safety marker armed and blocks the next launch.
The cost of that setting is that quitting becomes an explicit act, and these
tests hold both halves of the bargain: an ordinary close ends the process, a
close-for-the-game does not.
"""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from PySide6.QtCore import QTimer

from tests.guiharness import headless_window, qt_app, run_loop_until_quit


class ClosingTheWindowEndsTheProcessTests(unittest.TestCase):
    def test_closing_the_window_quits_the_application(self):
        app = qt_app()
        with headless_window() as window:
            window.show()
            QTimer.singleShot(0, window.close)
            self.assertTrue(
                run_loop_until_quit(app),
                "closing the launcher left the event loop running: with "
                "setQuitOnLastWindowClosed(False) the process outlives its "
                "own window unless closeEvent quits explicitly")

    def test_closing_for_the_game_leaves_the_loop_running(self):
        # The mirror image: the window goes, the launch thread keeps the
        # process alive until it reports back.
        app = qt_app()
        with headless_window(close_on_launch=True) as window:
            window.show()
            window.ui_state["launch_active"] = True
            QTimer.singleShot(0, window._close_for_game)
            self.assertFalse(
                run_loop_until_quit(app, timeout_ms=600),
                "the launcher quit while it was still supervising the game")
            self.assertTrue(window.ui_state["window_gone"])

    def test_the_supervising_thread_quits_once_the_game_is_gone(self):
        app = qt_app()
        with headless_window(close_on_launch=True) as window:
            window.show()
            window.ui_state["launch_active"] = True
            window._close_for_game()
            QTimer.singleShot(0, lambda: window._play_finished("closed"))
            self.assertTrue(
                run_loop_until_quit(app),
                "the window was gone and the game had exited, but nothing "
                "ended the process")


class RefusingToCloseTests(unittest.TestCase):
    """The guards are about not orphaning work, so they must not be
    reachable by the close-for-the-game path (which sets _force_close)."""

    def test_a_running_game_refuses_the_close(self):
        qt_app()
        with headless_window() as window:
            window.show()
            window.ui_state["launch_active"] = True
            with mock.patch.object(window, "warn_box") as warned:
                window.close()
            self.assertTrue(warned.called)
            self.assertTrue(window.isVisible(),
                            "the close was refused but the window went away")

    def test_a_busy_preparation_refuses_the_close(self):
        qt_app()
        with headless_window() as window:
            window.ui_state["busy"] = True
            with mock.patch.object(window, "warn_box") as warned:
                window.close()
            self.assertTrue(warned.called)

    def test_closing_for_the_game_bypasses_both_guards(self):
        qt_app()
        with headless_window(close_on_launch=True) as window:
            window.ui_state["launch_active"] = True
            window.ui_state["busy"] = True
            with mock.patch.object(window, "warn_box") as warned:
                window._close_for_game()
            self.assertFalse(
                warned.called,
                "the close-on-launch path hit the 'Minecraft is running' "
                "guard it is supposed to be exempt from")


if __name__ == "__main__":
    unittest.main()
