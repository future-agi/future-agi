import json
from pathlib import Path

# Resolve the demo fixtures relative to this file, not the process CWD.
# These loads run at import time, and this module is reached from
# accounts/migrations/0020_reseed_broken_demo_data.py, so a CWD-relative
# path makes any `migrate` (and any test that triggers it) fail with
# FileNotFoundError unless it happens to be run from `futureagi/`.
_DEMO_DATASET_DIR = Path(__file__).resolve().parent / "demo_dataset"

with open(_DEMO_DATASET_DIR / "table_data.json") as f:
    dataset_data = json.load(f)

with open(_DEMO_DATASET_DIR / "run_prompt_config.json") as f:
    run_prompt_config = json.load(f)

prompt_config = [
    {
        "name": "Generate Answer-1",
        "model": ["o1-mini"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Craft the most effective, clear, and concise answer (fewer than 50 words).\nQuestion: {{cf635e5b-92af-460d-a62b-8b22c64287d9}}\nDetails: {{4a502202-ad6e-4cea-a295-426cf977dfb4}}",
                    }
                ],
            }
        ],
        "configuration": {
            "top_p": 1,
            "max_tokens": 8190,
            "temperature": 0.5,
            "response_format": None,
            "presence_penalty": 1,
            "frequency_penalty": 1,
        },
    }
]

with open(_DEMO_DATASET_DIR / "experiment.json") as f:
    experiment_data = json.load(f)

with open(_DEMO_DATASET_DIR / "img_dataset_data.json") as f:
    image_dataset_data = json.load(f)
