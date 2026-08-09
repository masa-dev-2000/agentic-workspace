#!/usr/bin/env python3
from __future__ import annotations

import unittest

from route_material_format import normalize_format, route


class FormatRoutingTests(unittest.TestCase):
    def test_routes_each_use_moment(self) -> None:
        self.assertEqual(route("live")["output_format"], "pptx")
        self.assertEqual(route("async")["output_format"], "docx")
        self.assertEqual(route("fixed-distribution")["output_format"], "pdf")

    def test_explicit_format_wins(self) -> None:
        result = route("live", "word")
        self.assertEqual(result["output_format"], "docx")
        self.assertEqual(result["format_basis"], "explicit-user-request")

    def test_rejects_unknown_format(self) -> None:
        with self.assertRaises(ValueError):
            normalize_format("spreadsheet")


if __name__ == "__main__":
    unittest.main()
