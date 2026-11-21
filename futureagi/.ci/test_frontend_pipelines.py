"""The two frontend pipelines: the feature one may skip its unit tests only where the pull request one runs."""

import pathlib
import re
import unittest

WORKFLOWS = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows"
FEATURE = (WORKFLOWS / "frontend-feature.yml").read_text(encoding="utf-8")
DEVELOP = (WORKFLOWS / "frontend-develop.yml").read_text(encoding="utf-8")


def paths(workflow, event):
    """The `paths` of one trigger of a workflow's `on:` block."""
    triggers = workflow.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    block = re.split(r"(?m)^  \w+:\n?", triggers.split(f"  {event}:\n", 1)[1])[0]
    return re.findall(r'(?m)^      - "([^"]+)"$', block.split("    paths:\n", 1)[1])


class FrontendPipelinesTest(unittest.TestCase):
    def test_every_path_of_the_feature_pipeline_starts_the_pull_request_pipeline(self):
        # The feature pipeline skips `test:unit` while a pull request is open.
        # A push it runs for must therefore also start the pipeline that
        # runs every test: a change to the feature workflow itself did not.
        feature = paths(FEATURE, "push")
        self.assertIn("frontend/**", feature)
        self.assertIn(".github/workflows/frontend-feature.yml", feature)
        covered = paths(DEVELOP, "pull_request")
        for path in feature:
            self.assertIn(path, covered)

    def test_the_skip_is_the_only_one_and_asks_for_an_open_pull_request(self):
        self.assertEqual(FEATURE.count("if: steps.pr.outputs.open != 'true'"), 1)
        skipped = FEATURE.split("if: steps.pr.outputs.open != 'true'", 1)[1]
        self.assertTrue(skipped.lstrip().startswith("run: yarn test:unit"))
        # The pull request pipeline runs the whole suite on the same push.
        self.assertIn("run: yarn test:run --reporter=verbose", DEVELOP)
        # No branch filter on its pull_request trigger: every base branch.
        triggers = DEVELOP.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
        pull_request = triggers.split("  pull_request:\n", 1)[1].split(
            "  workflow_dispatch:", 1
        )[0]
        self.assertNotRegex(pull_request, r"(?m)^\s+branches(-ignore)?:")


if __name__ == "__main__":
    unittest.main()
