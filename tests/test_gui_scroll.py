"""Regression tests for Settings and version-picker wheel forwarding."""
# SPDX-License-Identifier: MIT

import ast
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from bol import gui
from bol.gui import _bind_x11_mousewheel_recursive, _enable_scrollable_frame_wheel


class Widget:
    def __init__(self, *children):
        self.children = children
        self.bindings = {}

    def bind(self, event, callback, add=None):
        self.bindings[event] = (callback, add)

    def winfo_children(self):
        return self.children


class Canvas:
    def __init__(self):
        self.scrolls = []

    def yview_scroll(self, amount, unit):
        self.scrolls.append((amount, unit))


class SettingsScrollTests(unittest.TestCase):
    def test_x11_wheel_is_forwarded_from_every_descendant(self):
        grandchild = Widget()
        child = Widget(grandchild)
        root = Widget(child)
        canvas = Canvas()

        _bind_x11_mousewheel_recursive(root, canvas)

        for widget in (root, child, grandchild):
            self.assertEqual(
                set(widget.bindings), {"<Button-4>", "<Button-5>"})
            self.assertNotIn("<MouseWheel>", widget.bindings)
            self.assertTrue(all(
                add == "+" for _, add in widget.bindings.values()))

        up = child.bindings["<Button-4>"][0]
        down = grandchild.bindings["<Button-5>"][0]
        self.assertEqual(up(SimpleNamespace(num=4)), "break")
        self.assertEqual(down(SimpleNamespace(num=5)), "break")
        self.assertEqual(canvas.scrolls, [(-1, "units"), (1, "units")])


class ScrollableFrameWheelTests(unittest.TestCase):
    def test_every_descendant_of_the_container_scrolls_the_frame(self):
        canvas = Canvas()
        row = Widget()
        frame = Widget(row)
        frame._parent_canvas = canvas
        search = Widget()
        popup = Widget(search, frame)

        self.assertTrue(_enable_scrollable_frame_wheel(frame, popup))

        for widget in (popup, search, frame, row):
            self.assertEqual(
                set(widget.bindings), {"<Button-4>", "<Button-5>"})
        row.bindings["<Button-4>"][0](SimpleNamespace(num=4))
        search.bindings["<Button-5>"][0](SimpleNamespace(num=5))
        self.assertEqual(canvas.scrolls, [(-1, "units"), (1, "units")])

    def test_frame_is_its_own_container_by_default(self):
        canvas = Canvas()
        child = Widget()
        frame = Widget(child)
        frame._parent_canvas = canvas

        self.assertTrue(_enable_scrollable_frame_wheel(frame))

        self.assertEqual(set(child.bindings), {"<Button-4>", "<Button-5>"})

    def test_a_frame_without_a_canvas_is_left_alone(self):
        child = Widget()
        frame = Widget(child)

        self.assertFalse(_enable_scrollable_frame_wheel(frame))
        self.assertEqual(child.bindings, {})


def _nested_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class PickerWheelWiringTests(unittest.TestCase):
    """The version picker builds its rows dynamically (issue #112)."""

    def setUp(self):
        source = Path(inspect.getsourcefile(gui)).read_text(encoding="utf-8")
        self.tree = ast.parse(source)

    def _calls_in(self, function_name):
        node = _nested_function(self.tree, function_name)
        self.assertIsNotNone(node, f"{function_name} not found in bol.gui")
        return {
            call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

    def test_version_picker_enables_wheel_scrolling(self):
        self.assertIn(
            "_enable_scrollable_frame_wheel", self._calls_in("open_picker"))

    def test_settings_tabs_keep_wheel_scrolling(self):
        self.assertIn(
            "_enable_scrollable_frame_wheel", self._calls_in("_build_settings"))


if __name__ == "__main__":
    unittest.main()
