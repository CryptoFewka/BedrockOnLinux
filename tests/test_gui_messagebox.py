"""Tests for the themed message box without requiring a display server."""
# SPDX-License-Identifier: MIT

import unittest

from bol.gui import (
    _ThemedMessageBox,
    _centered_window_position,
    _fit_dialog_size,
    _geometry_position,
    _messagebox_height,
    _select_monitor_bounds,
)


class FakeTclError(Exception):
    pass


class FakeTk:
    TclError = FakeTclError


class FakeOwner:
    def __init__(
            self, top=None, viewport=(1920, 1080),
            geometry=(0, 0, 980, 650)):
        self.top = top or self
        self.viewport = viewport
        self.geometry = geometry
        self.focused = False
        self.grabbed = False
        self.current_focus = None

    def winfo_toplevel(self):
        return self.top

    def winfo_exists(self):
        return True

    def focus_set(self):
        self.focused = True

    def focus_get(self):
        return self.current_focus

    def grab_set(self):
        self.grabbed = True

    def update_idletasks(self):
        pass

    def winfo_vrootwidth(self):
        return self.viewport[0]

    def winfo_vrootheight(self):
        return self.viewport[1]

    def winfo_vrootx(self):
        return 0

    def winfo_vrooty(self):
        return 0

    def winfo_rootx(self):
        return self.geometry[0]

    def winfo_rooty(self):
        return self.geometry[1]

    def winfo_width(self):
        return self.geometry[2]

    def winfo_height(self):
        return self.geometry[3]


class FakeWidget:
    def __init__(self, master=None, **options):
        self.master = master
        self.options = options
        self.pack_options = None
        self.text = ""
        self.focused = False

    def pack(self, **options):
        self.pack_options = options

    def insert(self, _position, text):
        self.text = text

    def configure(self, **options):
        self.options.update(options)

    def focus_set(self):
        self.focused = True


class FakeCtk:
    def __init__(self):
        self.frames = []
        self.labels = []
        self.textboxes = []

    def CTkFrame(self, master=None, **options):
        widget = FakeWidget(master, **options)
        self.frames.append(widget)
        return widget

    def CTkLabel(self, master=None, **options):
        widget = FakeWidget(master, **options)
        self.labels.append(widget)
        return widget

    def CTkTextbox(self, master=None, **options):
        widget = FakeWidget(master, **options)
        self.textboxes.append(widget)
        return widget


class FakeWindow:
    def __init__(self, action):
        self.action = action
        self.bindings = {}
        self.protocols = {}
        self.current_grab = None
        self.destroyed = False
        self.grabbed = False

    def grab_current(self):
        return self.current_grab

    def grab_set(self):
        self.current_grab = self
        self.grabbed = True

    def grab_release(self):
        self.current_grab = None

    def destroy(self):
        self.destroyed = True

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback

    def update_idletasks(self):
        pass

    def wait_visibility(self):
        pass

    def wait_window(self):
        if self.action == "WM_DELETE_WINDOW":
            self.protocols[self.action]()
        else:
            self.bindings[self.action](object())


class Theme:
    THEME_ACCENT = "accent"
    THEME_HOV = "accent-hover"
    GOLD = "gold"
    GOLD_HOV = "gold-hover"
    RED = "red"
    RED_HOV = "red-hover"
    FG = "foreground"
    SUB = "secondary"


class MessageBoxHarness:
    def __init__(
            self, action, viewport=(1920, 1080), window_scaling=1.0,
            owner_geometry=(0, 0, 980, 650), monitors=()):
        self.ctk = FakeCtk()
        self.root = FakeOwner(
            viewport=viewport, geometry=owner_geometry)
        self.window = FakeWindow(action)
        self.dialog_call = None
        self.buttons = []

        def dialog(title, width, height, parent=None, bounds=None):
            self.dialog_call = (title, width, height, parent, bounds)
            return self.window

        def mkbtn(parent, text, command, **options):
            button = FakeWidget(parent, text=text, command=command, **options)
            button.command = command
            self.buttons.append(button)
            return button

        self.messagebox = _ThemedMessageBox(
            self.ctk, FakeTk, self.root, Theme,
            lambda *args, **_kwargs: args, mkbtn, dialog,
            window_scaling=window_scaling,
            monitor_provider=lambda: monitors)


class ThemedMessageBoxTests(unittest.TestCase):
    def test_long_messages_are_bounded_and_use_a_scrollable_body(self):
        message = "x" * 8000
        harness = MessageBoxHarness("<Return>")

        self.assertEqual(
            harness.messagebox.showerror("Launch error", message), "ok")

        self.assertEqual(harness.dialog_call[1:3], (480, 520))
        self.assertEqual(harness.ctk.textboxes[0].text, message)
        self.assertEqual(
            harness.ctk.textboxes[0].options["state"], "disabled")
        self.assertTrue(
            harness.ctk.textboxes[0].options["activate_scrollbars"])
        self.assertEqual(harness.ctk.frames[1].pack_options["side"], "bottom")
        self.assertTrue(harness.buttons[0].focused)
        self.assertTrue(harness.window.grabbed)

    def test_high_scaling_keeps_long_dialog_inside_a_1366x768_screen(self):
        for scale, expected_size in (
                (1.5, (480, 426)), (2.0, (380, 320))):
            with self.subTest(scale=scale):
                harness = MessageBoxHarness(
                    "<Return>", viewport=(1366, 768),
                    window_scaling=scale)
                harness.messagebox.showerror("Launch error", "x" * 8000)

                self.assertEqual(harness.dialog_call[1:3], expected_size)
                self.assertLessEqual(expected_size[1] * scale, 768 - 80)

    def test_mixed_monitor_layout_uses_the_owners_smaller_monitor(self):
        monitors = (
            (0, 0, 2560, 1440),
            (2560, 0, 1366, 768),
        )
        harness = MessageBoxHarness(
            "<Return>", viewport=(3926, 1440), window_scaling=2.0,
            owner_geometry=(2700, 80, 900, 650), monitors=monitors)

        harness.messagebox.showerror("Launch error", "x" * 8000)

        self.assertEqual(harness.dialog_call[1:3], (380, 320))
        self.assertEqual(harness.dialog_call[4], monitors[1])
        x, y = _centered_window_position(
            (2700, 80, 900, 650), (760, 640), monitors[1])
        self.assertGreaterEqual(x, monitors[1][0])
        self.assertGreaterEqual(y, monitors[1][1])
        self.assertLessEqual(x + 760, monitors[1][0] + monitors[1][2])
        self.assertLessEqual(y + 640, monitors[1][1] + monitors[1][3])

    def test_parent_toplevel_owns_the_dialog_and_regains_focus(self):
        harness = MessageBoxHarness("<Return>")
        top = FakeOwner()
        child = FakeOwner(top)

        result = harness.messagebox.showinfo(
            "Information", "Done", parent=child)

        self.assertEqual(result, "ok")
        self.assertIs(harness.dialog_call[3], top)
        self.assertTrue(top.focused)

    def test_previous_focus_and_modal_grab_are_restored(self):
        harness = MessageBoxHarness("<Return>")
        previous_focus = FakeOwner()
        previous_grab = FakeOwner()
        harness.root.current_focus = previous_focus
        harness.window.current_grab = previous_grab

        harness.messagebox.showinfo("Information", "Done")

        self.assertTrue(previous_focus.focused)
        self.assertTrue(previous_grab.grabbed)

    def test_enter_and_keypad_enter_choose_yes(self):
        for sequence in ("<Return>", "<KP_Enter>"):
            with self.subTest(sequence=sequence):
                harness = MessageBoxHarness(sequence)
                self.assertTrue(
                    harness.messagebox.askyesno("Question", "Continue?"))

    def test_escape_and_window_close_choose_no(self):
        for action in ("<Escape>", "WM_DELETE_WINDOW"):
            with self.subTest(action=action):
                harness = MessageBoxHarness(action)
                self.assertFalse(
                    harness.messagebox.askyesno("Question", "Continue?"))

    def test_information_methods_follow_tkinter_return_contract(self):
        for method in ("showinfo", "showerror", "showwarning"):
            with self.subTest(method=method):
                harness = MessageBoxHarness("<Return>")
                self.assertEqual(
                    getattr(harness.messagebox, method)("Title", "Message"),
                    "ok",
                )

    def test_height_stays_usable_for_empty_and_multiline_messages(self):
        self.assertEqual(_messagebox_height(""), 210)
        self.assertEqual(_messagebox_height("\n".join(["line"] * 30)), 520)

    def test_dialog_size_also_caps_width_on_a_small_high_dpi_screen(self):
        self.assertEqual(
            _fit_dialog_size((480, 520), (800, 600), 2.0),
            (380, 260),
        )

    def test_monitor_selection_uses_owner_center_then_largest_overlap(self):
        monitors = (
            (-1920, 0, 1920, 1080),
            (0, 0, 2560, 1440),
            (2560, 0, 1366, 768),
        )
        self.assertEqual(
            _select_monitor_bounds(
                (2700, 100, 900, 650), monitors, (0, 0, 3926, 1440)),
            monitors[2],
        )
        self.assertEqual(
            _select_monitor_bounds(
                (2559, 0, 2, 2), monitors, (0, 0, 3926, 1440)),
            monitors[2],
        )
        self.assertEqual(
            _select_monitor_bounds(
                (-100, 100, 400, 500), monitors, (0, 0, 3926, 1440)),
            monitors[1],
        )
        self.assertEqual(
            _select_monitor_bounds(
                (0, 0, 980, 650), (), (0, 0, 1920, 1080)),
            (0, 0, 1920, 1080),
        )

    def test_centering_uses_scaled_window_size_and_virtual_desktop_bounds(self):
        self.assertEqual(
            _centered_window_position(
                (0, 0, 1000, 800), (960, 600), (0, 0, 1920, 1080)),
            (20, 100),
        )
        self.assertEqual(
            _centered_window_position(
                (-1920, 0, 1920, 1080), (480, 520),
                (-1920, 0, 3840, 1080)),
            (-1200, 280),
        )

    def test_geometry_position_accepts_customtkinter_negative_coordinates(self):
        self.assertEqual(_geometry_position("480x520+330+162"), (330, 162))
        self.assertEqual(_geometry_position("480x520+-1200+280"), (-1200, 280))
        self.assertEqual(_geometry_position("480x520-1200-40"), (-1200, -40))


if __name__ == "__main__":
    unittest.main()
