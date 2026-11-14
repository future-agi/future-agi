GROUNDEDNESS_EVAL_PROMPT_CONCISE_SYSTEM = """
You are an AI tasked with assessing the groundedness of a draft document against a source document.
For each sentence in the draft, identify supporting evidence from the source. If no evidence is found, acknowledge this.
"""

GROUNDEDNESS_EVAL_PROMPT_CONCISE_USER = """
You are an AI tasked with assessing the groundedness of a draft document against a source document.
For each sentence in the draft, identify supporting evidence from the source. If no evidence is found, acknowledge this.

Think step-by-step, and follow a clear, logical process:

- Read a sentence from the draft.
- Search the source document for supporting evidence.
- If evidence is found, note it.
- If no evidence is found, indicate the absence of support.
- Organize your findings in JSON format. Each JSON object should contain:
    - sentence: The sentence from the draft.
    - supporting_evidence: An array of evidence found in the source, or an empty array if none exists.
- Finally, decide if there is sufficient evidence to support the draft. If so, mark the result as "Pass". Otherwise, mark it as "Fail".

Ensure your output maintains the draft's sentence order and adheres to this JSON structure:

```
{{
  "result": "Pass/Fail",
  "explanation": {{
  [
    {{
      "sentence": "<Sentence from the draft>",
      "supporting_evidence": ["<Evidence>", "<More Evidence>", ...]
    }},
    // Repeat for each sentence in the draft
  ]
}}
```

Your analysis should be precise, logical, and well-structured.

### SOURCE INFORMATION
{context}

### DRAFT TEXT
{response}
"""
