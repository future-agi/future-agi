#!/usr/bin/env python3
"""Standalone verification for taxonomy-driven persona generation."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../futureagi"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings")

try:
    import django

    django.setup()
except Exception as exc:
    print(f"Django setup skipped: {exc}")

from collections import Counter

from simulate.constants.edge_case_taxonomy import EDGE_CASE_TAXONOMY
from simulate.services.persona_generator import generate_personas, taxonomy_coverage


def main():
    start = time.time()
    personas = generate_personas("refund and billing support", n=50, seed=42)
    elapsed = time.time() - start
    histogram = Counter(p["edge_case"] for p in personas)
    print("Generated:", len(personas))
    print("Coverage:", taxonomy_coverage(personas))
    print(" taxonomy keys:", len(EDGE_CASE_TAXONOMY))
    for item in EDGE_CASE_TAXONOMY:
        print(f"  {item['key']}: {histogram.get(item['key'], 0)}")
    print(f"Time for 50: {elapsed:.3f}s avg {(elapsed / 50) * 1000:.2f}ms")
    again = generate_personas("refund and billing support", n=50, seed=42)
    assert again == personas, "not deterministic"
    print("Determinism: PASS")
    assert taxonomy_coverage(personas) == 1.0, "taxonomy not fully covered"
    print("OVERALL PASS")


if __name__ == "__main__":
    main()
