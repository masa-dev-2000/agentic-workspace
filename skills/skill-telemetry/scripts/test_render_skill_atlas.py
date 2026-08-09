from __future__ import annotations

import unittest

import render_skill_atlas


class RenderSkillAtlasTests(unittest.TestCase):
    def test_registry_network_and_history_render(self) -> None:
        data = render_skill_atlas.load_data(render_skill_atlas.ROOT / "skill-registry.yaml")
        page = render_skill_atlas.render(data)
        self.assertEqual(data["summary"]["skills"], len(data["skills"]))
        self.assertGreater(data["summary"]["skills"], 0)
        self.assertIn("build-decision-ready-materials", page)
        self.assertIn('id="overviewTab"', page)
        self.assertIn('id="networkTab"', page)
        self.assertIn('id="historyTab"', page)
        self.assertIn("AIは、この順番でお手伝いします", page)
        self.assertIn("押して、この段階を見る", page)
        self.assertIn("知見の歴史", page)
        self.assertIn("Skillは、問題と学びから育ってきました", page)
        self.assertIn("現在だけ確認・歴史未記録", page)
        self.assertIn("個人知能・判断継承", page)
        self.assertIn("相談する", page)
        self.assertIn("覚えて改善する", page)
        self.assertIn("くわしい技術情報", page)
        self.assertTrue(
            all(
                skill["plain_name"]
                and skill["plain_summary"]
                and skill["flow_stage"] in {1, 2, 3, 4, 5}
                for skill in data["skills"]
            )
        )
        self.assertEqual({1, 2, 3, 4, 5}, {skill["flow_stage"] for skill in data["skills"]})
        self.assertEqual(
            {"understand", "manage", "create", "verify", "operate", "learn", "clone"},
            {skill["category"] for skill in data["skills"]},
        )
        self.assertIn("canonical-registry", page)
        self.assertNotIn("<script src=", page)


if __name__ == "__main__":
    unittest.main()
