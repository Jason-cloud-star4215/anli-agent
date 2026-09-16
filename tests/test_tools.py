import unittest
from pathlib import Path

from anli.models import Role
from anli.tools import InvalidArguments, ToolCall, ToolNotAllowed, ToolRouter, UnknownTool
from anli.tools_builtin import build_default_registry
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class ToolGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry = build_default_registry(
            LawStore.load(DATA / "laws.seed.json"),
            CaseStore.load(DATA / "cases.seed.json"),
        )
        cls.router = ToolRouter(registry)

    def test_unknown_tool(self):
        with self.assertRaises(UnknownTool):
            self.router.call(Role.RESEARCHER, ToolCall(tool="delete_files", arguments={}))

    def test_role_allowlist_blocks_tool(self):
        with self.assertRaises(ToolNotAllowed):
            self.router.call(
                Role.REDTEAM,
                ToolCall(tool="calc_damages", arguments={"items": {"a": 1}}),
            )

    def test_invalid_arguments(self):
        with self.assertRaises(InvalidArguments):
            self.router.call(Role.RESEARCHER, ToolCall(tool="law_lookup", arguments={}))

    def test_trace_recorded(self):
        self.router.call(
            Role.RESEARCHER,
            ToolCall(tool="law_lookup", arguments={"keywords": ["交通事故"]}),
        )
        self.assertTrue(self.router.trace[-1].ok)


if __name__ == "__main__":
    unittest.main()
