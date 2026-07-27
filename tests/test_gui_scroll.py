"""Regression tests for Settings wheel forwarding."""
# SPDX-License-Identifier: MIT

import unittest
from types import SimpleNamespace

from bol.gui import _bind_x11_mousewheel_recursive


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


if __name__ == "__main__":
    unittest.main()
