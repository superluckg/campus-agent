import unittest
from app.intent.signals import IntentSignal, LLMDecisionConverter
from app.routing.router import AgentRouter
from app.schemas.enums import AgentName, IntentName
from app.schemas.intent import IntentResult

class IntentAndRoutingTests(unittest.TestCase):
    def test_ambiguous_single_signal_clarifies(self):
        result = LLMDecisionConverter(clarification_threshold=.2).convert(IntentSignal('llm',
            {IntentName.CAREER_RESUME: .8, IntentName.LEARNING_PLAN: .75}, {}))
        self.assertEqual(result.primary_intent, IntentName.UNCLEAR)
        self.assertEqual(AgentRouter().route(result).route, 'clarify')

    def test_compound_selects_at_most_two_distinct_agents(self):
        result = IntentResult(primary_intent='compound', confidence=1, is_compound=True,
            sub_intents=['career_resume', 'learning_gpa'],
            candidate_scores={'career_resume': .8, 'learning_gpa': .7, 'campus_network': .6})
        route = AgentRouter().route(result)
        self.assertEqual(route.route, 'multi_agent')
        self.assertEqual([item.agent_name for item in route.assignments], [AgentName.CAREER, AgentName.LEARNING])
