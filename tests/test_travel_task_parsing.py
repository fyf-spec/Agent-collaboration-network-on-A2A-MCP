from __future__ import annotations

from datetime import date, timedelta
import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import coordinator
from agents.base_agent import extract_city
from agents.request_parser import extract_travel_task_from_context
from mcp_servers.mock_data import get_packing_list, search_attractions


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

    def test_packing_temperature_range_does_not_parse_range_dash_as_negative(self) -> None:
        result = get_packing_list(city="南京", days=3, temperature="22.0°C-31.2°C", condition="阴")
        clothing = next(item for item in result["packing_list"] if item["category"] == "衣物")

        self.assertIn("短袖", clothing["items"])
        self.assertNotIn("羽绒服", clothing["items"])
        self.assertNotIn("保暖内衣", clothing["items"])

    def test_agent_parser_resolves_relative_day_start_date(self) -> None:
        task = extract_travel_task_from_context("后天从上海去南京玩3天，要求玩的舒适，必须去总统府看看。", {})

        self.assertEqual((date.today() + timedelta(days=2)).isoformat(), task["start_date"])
        self.assertEqual("后天", task["date_text"])

    def test_no_llm_coordinator_context_propagates_rule_parsed_date(self) -> None:
        old_use_llm = os.environ.get("A2A_USE_LLM")
        os.environ["A2A_USE_LLM"] = "0"
        original_discovery = coordinator._fetch_discovered_agents
        coordinator._fetch_discovered_agents = lambda: {}
        try:
            question = "后天从上海去南京玩3天，要求玩的舒适，必须去总统府看看。"
            travel_task, workflow_dag = coordinator.extract_travel_task(question, {})
            targets = [node["agent"] for node in workflow_dag]
            plan = coordinator.build_dependency_plan(question, targets, travel_task, workflow_dag)
            state = coordinator.CoordinatorState(host="127.0.0.1", port=0, tcp_port=0)
            record = state.create_task(question, targets, 10.0, plan=plan)
            context = coordinator.build_node_context(
                record=record,
                node_id="weather_agent",
                stage="weather_agent_processing",
                dependencies=[],
                inputs={},
                target="weather_agent",
            )
        finally:
            coordinator._fetch_discovered_agents = original_discovery
            if old_use_llm is None:
                os.environ.pop("A2A_USE_LLM", None)
            else:
                os.environ["A2A_USE_LLM"] = old_use_llm

        expected = (date.today() + timedelta(days=2)).isoformat()
        self.assertEqual(expected, travel_task["start_date"])
        self.assertEqual(expected, plan["travel_task"]["start_date"])
        self.assertEqual(expected, context["travel_task"]["start_date"])
        self.assertIn("总统府", travel_task["must_visit"])


if __name__ == "__main__":
    unittest.main()
