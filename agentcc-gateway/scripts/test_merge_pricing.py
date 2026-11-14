"""Run from agentcc-gateway/: python3 -m unittest discover -s scripts"""
import unittest

import merge_pricing as mp


def models_dev(provider_id, models):
    return {provider_id: {"models": models}}


class ConvertTest(unittest.TestCase):
    def test_provider_prefix_and_unit_conversion(self):
        api = models_dev("perplexity-agent", {
            "google/gemini-4-argon": {
                "cost": {"input": 2, "output": 10, "cache_read": 0.1},
                "limit": {"context": 1048576, "output": 65536},
                "tool_call": True,
            },
        })
        entries, skipped = mp.convert(api)
        entry = entries["perplexity/google/gemini-4-argon"]
        self.assertEqual(skipped, 0)
        self.assertEqual(entry["litellm_provider"], "perplexity")
        self.assertEqual(entry["mode"], "responses")
        self.assertAlmostEqual(entry["input_cost_per_token"], 2e-6)
        self.assertAlmostEqual(entry["output_cost_per_token"], 1e-5)
        self.assertAlmostEqual(entry["cache_read_input_token_cost"], 1e-7)
        self.assertEqual(entry["max_input_tokens"], 1048576)
        self.assertEqual(entry["source"], "models.dev")

    def test_provider_table(self):
        model = {"cost": {"input": 1, "output": 2}}
        for provider_id, key, provider in [
            ("google", "gemini/m", "gemini"),
            ("google-vertex", "vertex_ai/m", "vertex_ai"),
        ]:
            entries, _ = mp.convert(models_dev(provider_id, {"m": model}))
            self.assertEqual(entries[key]["litellm_provider"], provider)
            self.assertEqual(entries[key]["mode"], "chat")

    def test_unmapped_provider_is_ignored(self):
        entries, skipped = mp.convert(models_dev("some-reseller", {"m": {"cost": {"input": 1}}}))
        self.assertEqual((entries, skipped), ({}, 0))

    def test_unexpressible_price_is_skipped_and_counted(self):
        api = models_dev("google", {
            "tiered": {"cost": {"input": 2, "output": 12, "tiers": [{"input": 4}]}},
            "cache-write": {"cost": {"input": 2, "output": 12, "cache_write": 3}},
            "free": {"cost": {"input": 0, "output": 0}},
            "ok": {"cost": {"input": 2, "output": 12}},
        })
        entries, skipped = mp.convert(api)
        self.assertEqual(list(entries), ["gemini/ok"])
        self.assertEqual(skipped, 3)

    def test_embedding_mode(self):
        api = models_dev("google-vertex", {
            "emb": {"type": "embedding", "cost": {"input": 0.15, "output": 0}, "limit": {"context": 2048, "output": 1}},
        })
        entry = mp.convert(api)[0]["vertex_ai/emb"]
        self.assertEqual(entry["mode"], "embedding")
        self.assertNotIn("max_output_tokens", entry)


class MergeTest(unittest.TestCase):
    API = models_dev("google", {
        "shared": {"cost": {"input": 9, "output": 9}},
        "new": {"cost": {"input": 1, "output": 2}},
    })

    def test_upstream_wins_over_models_dev(self):
        upstream = {"gemini/shared": {"input_cost_per_token": 1e-6}}
        merged, counts = mp.merge(upstream, {}, self.API)
        self.assertEqual(merged["gemini/shared"], {"input_cost_per_token": 1e-6})
        self.assertEqual(merged["gemini/new"]["source"], "models.dev")
        self.assertEqual(counts["models.dev"], 1)

    def test_models_dev_entry_is_derived_again_not_kept(self):
        stale = {"source": "models.dev", "input_cost_per_token": 1.0}
        current = {"gemini/gone": stale, "gemini/new": stale}
        merged, _ = mp.merge({}, current, self.API)
        self.assertNotIn("gemini/gone", merged)
        self.assertAlmostEqual(merged["gemini/new"]["input_cost_per_token"], 1e-6)

    def test_upstream_taking_over_replaces_models_dev_entry(self):
        current = {"gemini/new": {"source": "models.dev", "input_cost_per_token": 1e-6}}
        upstream = {"gemini/new": {"input_cost_per_token": 5e-6}}
        merged, _ = mp.merge(upstream, current, self.API)
        self.assertEqual(merged["gemini/new"], {"input_cost_per_token": 5e-6})

    def test_entry_upstream_dropped_is_kept(self):
        current = {"old": {"input_cost_per_token": 3e-6}}
        merged, counts = mp.merge({}, current, {})
        self.assertEqual(merged["old"], {"input_cost_per_token": 3e-6})
        self.assertEqual(counts["kept"], 1)

    def test_true_capability_flag_is_carried_forward(self):
        current = {"m": {"supports_vision": True}}
        merged, counts = mp.merge({"m": {"input_cost_per_token": 1e-6}}, current, {})
        self.assertTrue(merged["m"]["supports_vision"])
        self.assertEqual(counts["flags carried"], 1)


if __name__ == "__main__":
    unittest.main()
