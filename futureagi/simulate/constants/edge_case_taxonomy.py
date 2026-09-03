"""Edge-case taxonomy for taxonomy-driven persona generation.

Each category carries a weight for sampling and a prompt fragment that is
appended to the persona additional_instruction. Categories are deliberately
framework agnostic so text and voice simulators can share them.
"""

EDGE_CASE_TAXONOMY = [
    {
        "key": "payment_dispute",
        "label": "Payment dispute",
        "weight": 3,
        "prompt": "Raise a billing or payment dispute with order dates and amounts.",
    },
    {
        "key": "auth_failure",
        "label": "Auth failure",
        "weight": 2,
        "prompt": "Fail authentication at least once, then ask for recovery steps.",
    },
    {
        "key": "interruption",
        "label": "Interruption heavy",
        "weight": 2,
        "prompt": "Interrupt with follow-up questions before the agent finishes.",
    },
    {
        "key": "multi_intent",
        "label": "Multi intent",
        "weight": 2,
        "prompt": "Combine two intents in one turn, for example refund plus address change.",
    },
    {
        "key": "vague_request",
        "label": "Vague request",
        "weight": 2,
        "prompt": "Start vague and force the agent to ask clarifying questions.",
    },
    {
        "key": "policy_refusal",
        "label": "Policy refusal",
        "weight": 1,
        "prompt": "Request something disallowed so the agent must refuse safely.",
    },
    {
        "key": "accent_noise",
        "label": "Accent and noise",
        "weight": 1,
        "prompt": "Use short informal messages with typos as if on a noisy connection.",
    },
    {
        "key": "escalation",
        "label": "Escalation demand",
        "weight": 1,
        "prompt": "Demand a human agent after two failed attempts.",
    },
]

EDGE_CASE_KEYS = [c["key"] for c in EDGE_CASE_TAXONOMY]


def get_edge_case(key):
    for item in EDGE_CASE_TAXONOMY:
        if item["key"] == key:
            return item
    raise KeyError(f"Unknown edge case: {key}")
