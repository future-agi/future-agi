import json
from pathlib import Path

DEMO_DATASET = Path(__file__).resolve().parent / "demo_dataset"

with (DEMO_DATASET / "table_data.json").open() as f:
    dataset_data = json.load(f)

with (DEMO_DATASET / "run_prompt_config.json").open() as f:
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

with (DEMO_DATASET / "experiment.json").open() as f:
    experiment_data = json.load(f)

with (DEMO_DATASET / "img_dataset_data.json").open() as f:
    image_dataset_data = json.load(f)
