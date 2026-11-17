"""Pin the masking basis named by the source disclosures without Django setup."""

import ast
import runpy
import unittest
from pathlib import Path

AGENTCC = Path(__file__).resolve().parents[1]


class RedactionBasisCopyTests(unittest.TestCase):
    def test_trace_content_disclosure_names_masking_basis(self):
        disclosure = runpy.run_path(str(AGENTCC / "trace_content.py"))["DISCLOSURE"]
        self.assertEqual(
            disclosure,
            "Full-content capture exports tool inputs and outputs to your organization's collector. It masks secret-named fields and recognized Bearer, sk-, AIza, AKIA/ASIA and JWT formats. Prefix-less Future AGI keys can remain in other text. Other sensitive content may still be exported. Off by default; only an organization Owner or Admin can enable it. Enabling and disabling are audited.",
        )

    def test_api_key_entity_label_names_prefixes(self):
        # This reference data is static; importing the view would require Django.
        tree = ast.parse((AGENTCC / "views" / "guardrail_config.py").read_text())
        entities = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "PII_ENTITY_TYPES"
                for target in node.targets
            )
        )
        labels = [entity["label"] for entity in entities if entity["id"] == "API_KEY"]
        self.assertEqual(
            labels,
            [
                "API key with an sk, key or token prefix and - or _ (not prefix-less Future AGI keys)"
            ],
        )


if __name__ == "__main__":
    unittest.main()
