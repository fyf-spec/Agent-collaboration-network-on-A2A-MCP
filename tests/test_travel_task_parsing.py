from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import coordinator
from agents.base_agent import extract_city
from mcp_servers.mock_data import search_attractions


class TravelTaskParsingTests(unittest.TestCase):
    def test_rule_fallback_preserves_non_default_destination(self) -> None:
        task = coordinator._extract_travel_task_by_rules("后天从上海去云南玩3天，要求穷游并且尽量乘坐地铁。")

        self.assertEqual("上海", task["origin_city"])
        self.assertEqual("云南", task["destination_city"])
        self.assertEqual(3, task["days"])
        self.assertEqual("low", task["budget_level"])
        self.assertEqual("public_transport", task["transport_preference"])

    def test_llm_normalization_uses_explicit_destination_from_question(self) -> None:
        normalized = coordinator._normalize_travel_task(
            {"origin_city": "上海", "destination_city": "北京", "days": 3},
            {"_raw_question": "后天从上海去云南玩3天，要求穷游并且尽量乘坐地铁。"},
            parser="test_parser",
        )

        self.assertEqual("上海", normalized["origin_city"])
        self.assertEqual("云南", normalized["destination_city"])

    def test_agent_city_extraction_prefers_destination_not_origin(self) -> None:
        self.assertEqual("黄山", extract_city("春节假期从上海去黄山玩，要求参观重要景区。", {}))
        self.assertEqual("厦门", extract_city("从南京到厦门玩两天，低预算。", {}))

    def test_mock_attractions_do_not_relabel_unknown_city_as_beijing(self) -> None:
        result = search_attractions(city="云南", days=3)

        self.assertEqual("云南", result["city"])
        self.assertEqual("云南", result["requested_city"])
        self.assertTrue(result["fallback_used"])


if __name__ == "__main__":
    unittest.main()
