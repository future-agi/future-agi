"""Deterministic taxonomy-driven persona generator.

Expands the 18 static SYSTEM personas into workspace-level personas by
crossing demographics with the edge-case taxonomy. Pure Python with a
seeded RNG so output is reproducible and testable without LLM or GPU.
"""

import hashlib
import random
import re

from simulate.constants.edge_case_taxonomy import EDGE_CASE_TAXONOMY

GENDERS = ["male", "female"]
AGE_GROUPS = ["18-25", "25-32", "32-40", "40-50", "50-60", "60+"]
OCCUPATIONS = [
    "Student",
    "Teacher",
    "Engineer",
    "Doctor",
    "Nurse",
    "Business Owner",
    "Manager",
    "Accountant",
    "Freelancer",
    "Retired",
]
LOCATIONS = ["United States", "Canada", "United Kingdom", "Australia", "India"]
PERSONALITIES = [
    "Friendly and cooperative",
    "Professional and formal",
    "Cautious and skeptical",
    "Impatient and direct",
    "Detail-oriented",
    "Anxious",
    "Confident",
    "Analytical",
]
STYLES = [
    "Direct and concise",
    "Detailed and elaborate",
    "Casual and friendly",
    "Formal and polite",
    "Technical",
    "Questioning",
]


def _slug(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:48]


def _weighted_edge_cases(rng):
    keys = [c["key"] for c in EDGE_CASE_TAXONOMY]
    weights = [c["weight"] for c in EDGE_CASE_TAXONOMY]
    return rng.choices(keys, weights=weights, k=1)[0]


def generate_personas(scenario_source="", n=50, seed=42):
    """Generate n workspace persona payloads.

    Args:
        scenario_source: free text describing the scenario under test.
        n: number of personas to generate.
        seed: RNG seed for reproducibility.

    Returns:
        List of dicts matching the workspace persona payload shape.
    """
    rng = random.Random(seed)
    source = (scenario_source or "general support").strip()
    source_slug = _slug(source) or "general"
    seen = set()
    out = []
    attempts = 0
    while len(out) < n and attempts < n * 20:
        attempts += 1
        edge_key = _weighted_edge_cases(rng)
        edge = next(c for c in EDGE_CASE_TAXONOMY if c["key"] == edge_key)
        gender = rng.choice(GENDERS)
        age = rng.choice(AGE_GROUPS)
        occupation = rng.choice(OCCUPATIONS)
        location = rng.choice(LOCATIONS)
        personality = rng.choice(PERSONALITIES)
        style = rng.choice(STYLES)
        fingerprint = hashlib.md5(
            f"{gender}|{age}|{occupation}|{location}|{personality}|{style}|{edge_key}".encode()
        ).hexdigest()[:12]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        name = f"{personality.split()[0]} {occupation} ({edge['label']})"
        out.append(
            {
                "name": name[:255],
                "description": f"Generated for '{source_slug}': {personality.lower()} {occupation.lower()} exercising {edge['label'].lower()}.",
                "persona_type": "workspace",
                "gender": [gender],
                "age_group": [age],
                "occupation": [occupation],
                "location": [location],
                "personality": [personality],
                "communication_style": [style],
                "languages": ["English"],
                "edge_case": edge_key,
                "additional_instruction": f"{edge['prompt']} Stay in character as {personality.lower()} and {style.lower()}.",
                "generator": "persona_generator_v1",
                "generator_seed": seed,
                "source_slug": source_slug,
            }
        )
    return out


def taxonomy_coverage(personas):
    """Return fraction of taxonomy keys covered by a persona list."""
    if not personas:
        return 0.0
    covered = {p.get("edge_case") for p in personas if p.get("edge_case")}
    total = len(EDGE_CASE_TAXONOMY)
    return len(covered) / total if total else 0.0
