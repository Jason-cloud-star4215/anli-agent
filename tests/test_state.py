import unittest

from anli.models import TaskState
from anli.state import Budget, BudgetExceeded, IllegalTransition, StateStore, transition


class StateMachineTest(unittest.TestCase):
    def test_main_chain_advances(self):
        state = transition(TaskState.RECEIVED, TaskState.CLARIFYING)
        self.assertEqual(state, TaskState.CLARIFYING)

    def test_illegal_transition_rejected(self):
        with self.assertRaises(IllegalTransition):
            transition(TaskState.RECEIVED, TaskState.DELIVERED)

    def test_branch_and_resume(self):
        state = transition(TaskState.RESEARCHING, TaskState.NEEDS_INFO)
        self.assertEqual(state, TaskState.NEEDS_INFO)
        resumed = transition(TaskState.NEEDS_INFO, TaskState.RESEARCHING)
        self.assertEqual(resumed, TaskState.RESEARCHING)

    def test_budget_stops_runaway_loop(self):
        budget = Budget(max_turns=2)
        budget.spend_turn()
        budget.spend_turn()
        with self.assertRaises(BudgetExceeded):
            budget.spend_turn()

    def test_checkpoint_roundtrip(self):
        store = StateStore()
        store.save("t1", TaskState.RESEARCHING, {"artifacts": []})
        record = store.load("t1")
        self.assertEqual(record["state"], "RESEARCHING")


if __name__ == "__main__":
    unittest.main()
