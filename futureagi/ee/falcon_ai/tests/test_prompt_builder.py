import re

from ee.falcon_ai import prompt_builder as prompt_builder_module
from ee.falcon_ai.prompt_builder import PromptBuilder

_SET_SIZE = re.compile(
    r"the set is (\d+) evals: (\d+) from the Future AGI built-in suite "
    r"and (\d+) written for this project"
)


class TestOutputFormat:
    """The chat renders GitHub-flavoured markdown and a substantive answer is
    rendered as a branded document. Nothing in the prompt used to say either,
    and answers came back as one unbroken block of prose."""

    def _prompt(self):
        return PromptBuilder().build(
            mode=None,
            skill=None,
            memories=None,
            tools=[],
            context=None,
            workspace_name="Default Workspace",
            user_email="someone@example.com",
        )

    def test_the_prompt_says_how_to_lay_an_answer_out(self):
        prompt = self._prompt()
        assert "OUTPUT FORMAT:" in prompt
        assert "markdown" in prompt

    def test_it_asks_for_tables_headings_and_short_paragraphs(self):
        prompt = self._prompt()
        for rule in ("A table for three or more items", "`## The section`",
                     "two or three sentences"):
            assert rule in prompt, rule

    def test_it_names_every_block_the_renderer_understands(self):
        prompt = self._prompt()
        for block in ("# Title", "## 01 - The section", "```stats", "```prompt",
                      "blockquote", "italics", "`---`"):
            assert block in prompt, block

    def test_it_reserves_the_report_for_answers_worth_forwarding(self):
        prompt = self._prompt()
        assert "A greeting, a one-line answer or a clarifying question stops there" in prompt
        assert "forward to their team" in prompt

    def test_it_holds_figures_to_what_a_tool_returned(self):
        assert "traces to a tool result, never an" in self._prompt()

    def test_there_is_one_section_governing_the_answer_shape(self):
        assert self._prompt().count("OUTPUT FORMAT:") == 1

    def test_it_survives_a_call_with_no_workspace_extras(self):
        assert self._prompt().startswith("You are Falcon AI")


class TestEvalSetShape:
    """A recommended eval set has a stated size and split. Left open, the same
    readiness question answered with a different count every run, and nothing
    stopped an answer being five bespoke evals and no built-in one."""

    def _prompt(self):
        return PromptBuilder().build(
            mode=None,
            skill=None,
            memories=None,
            tools=[],
            context=None,
            workspace_name="Default Workspace",
            user_email="someone@example.com",
        )

    def _counts(self, prompt):
        match = _SET_SIZE.search(prompt)
        assert match, "the prompt never states the size of the set"
        return tuple(int(g) for g in match.groups())

    def test_it_states_the_size_and_the_split(self):
        total, built_in, custom = self._counts(self._prompt())
        assert total == built_in + custom
        assert (total, built_in, custom) == (
            prompt_builder_module.EVAL_SET_TOTAL_COUNT,
            prompt_builder_module.EVAL_SET_BUILT_IN_COUNT,
            prompt_builder_module.EVAL_SET_CUSTOM_COUNT,
        )

    def test_every_eval_in_the_set_is_agent_as_a_judge(self):
        prompt = self._prompt()
        assert "Every eval in the set is agent-as-a-judge" in prompt
        assert f"eval_type '{prompt_builder_module.EVAL_SET_TYPE}'" in prompt

    def test_the_numbers_follow_the_constants(self, monkeypatch):
        monkeypatch.setattr(prompt_builder_module, "EVAL_SET_BUILT_IN_COUNT", 7)
        monkeypatch.setattr(prompt_builder_module, "EVAL_SET_CUSTOM_COUNT", 4)
        monkeypatch.setattr(prompt_builder_module, "EVAL_SET_TOTAL_COUNT", 11)

        prompt = self._prompt()
        assert self._counts(prompt) == (11, 7, 4)
        assert "Find the 7 built-in ones" in prompt

    def test_there_is_one_section_governing_the_set(self):
        assert self._prompt().count("EVAL SET SHAPE:") == 1
