from ee.falcon_ai.prompt_builder import PromptBuilder


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
