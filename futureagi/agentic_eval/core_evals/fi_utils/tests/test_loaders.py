import json
import pytest
from agentic_eval.core_evals.fi_loaders.loader import Loader

def test_jsonl_loader_happy_path(tmp_path):
    # Create a temporary jsonl file
    file_path = tmp_path / "test_dataset.jsonl"
    
    data = [
        {"query": "What is AGI?", "response": "Artificial General Intelligence", "expected_response": "AGI"},
        {"query": "Hello", "response": "Hi there", "expected_response": "Hello!"}
    ]
    
    with open(file_path, "w") as f:
        for item in data:
            f.write(json.dumps(item) + "\n")
            
    # Initialize the loader
    loader = Loader()
    
    # Load and process the jsonl file
    result = loader.load("jsonl", filename=str(file_path))
    
    # Verify the results
    assert len(result) == 2
    
    assert result[0]["query"] == "What is AGI?"
    assert result[0]["response"] == "Artificial General Intelligence"
    assert result[0]["expected_response"] == "AGI"
    assert result[0]["context"] is None
    
    assert result[1]["query"] == "Hello"
    assert result[1]["response"] == "Hi there"
    assert result[1]["expected_response"] == "Hello!"
    assert result[1]["context"] is None
