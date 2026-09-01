from ee.falcon_ai.prompt_builder import PromptBuilder


class TestOutputFormat:
    """The chat renders GitHub-flavoured markdown. Nothing in the prompt used
    to say so, and answers came back as one unbroken block of prose."""

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
        for rule in ("markdown table", "`##` headings", "two or three sentences"):
            assert rule in prompt, rule

    def test_it_survives_a_call_with_no_workspace_extras(self):
        assert self._prompt().startswith("You are Falcon AI")
