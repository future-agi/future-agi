### INPUT_SELECTION_PROMPT

INPUT_SELECTION_PROMPT = """You are an input selection expert. Your task is to select a **single** input key from the provided dictionary of fields that is most suitable for error detection based on the provided evaluation criteria.

**The user provided the following criteria on which the input was evaluated**

<Evaluation Name>
{eval_name}
</Evaluation Name>

<Evaluation Criteria>
{rule_prompt}
</Evaluation Criteria>

**Fields Dictionary:**
<Field Keys and Types>
{input_keys}
</Field Keys and Types>

You only need to return the input key that is most suitable for error detection. This input key will be used by the error localization expert to detect errors. In case of multimodal inputs, select the key that contains the multimodal data (e.g., image or audio) if it is relevant to the evaluation criteria.

**Output Format:**
<selected_input_key>
input_key
</selected_input_key>

EXAMPLE:

<Evaluation Name>
Translation Accuracy
</Evaluation Name>

<Evaluation Criteria>
"Check if the language translation accurately conveys the meaning and context of the input in the output."
</Evaluation Criteria>

**Dictionary of Input keys:**
<Field Keys and Types>
{{"input": "text", "output": "text"}}
</Field Keys and Types>
(input and output are the keys for the input and output of the evaluation and text is the type of data they contain)


Output:
<selected_input_key>
output
</selected_input_key>

RULES:
1. If there is an 'output' key in the Field Keys, select it as the most suitable for error detection.
2. Provide only the input key relevant to the evaluation. Do not include any additional information or explanation.
3. Do not select key labeled as 'criteria', as this represents evaluation criteria.
4. Use the 'Evaluation Name' and 'Evaluation Criteria' to determine the appropriate input key.
5. For evaluations involving both image and text inputs, or audio and text inputs, prioritize the multimodal key (image or audio) unless the evaluation criteria specifically indicate otherwise.
6. For example:
   - In translation accuracy evaluation, the key would be the output key containing the translated sentence.
   - In image captioning evaluation the key would be the output key where the data type is text.
   - In image instruction evaluation task the key would be the output key where the data type is image.
   - In context relevance evaluation, the key would be the context key containing the text data.
7. Always return the key, not the value.
"""


_SYSTEM_TMPL = (
    "You are the error localizer for {modality} inputs. The upstream "
    "evaluator already judged this case and produced the result and "
    "explanation shown to you — do NOT re-judge it. Your only job is "
    "to identify which {unit_label_plural} of the selected input "
    "caused that verdict.\n"
    "The ``unit_key`` field is an internal identifier. Your user-facing "
    "text (``reason``, ``improvement``, ``rank_reason``) must describe "
    "the region in plain language — {describe} — and must never "
    "reference {unit_prefix}_N, whole_{modality}, or any other "
    "internal key."
)

SYSTEM_PROMPT = {
    "text": _SYSTEM_TMPL.format(
        modality="text",
        unit_label_plural="sentences",
        unit_prefix="sentence",
        describe="quote or paraphrase the actual sentence content",
    ),
    "audio": _SYSTEM_TMPL.format(
        modality="audio",
        unit_label_plural="time segments",
        unit_prefix="segment",
        describe="describe the audio by its timestamp range",
    ),
    "image": _SYSTEM_TMPL.format(
        modality="image",
        unit_label_plural="patches",
        unit_prefix="patch",
        describe="describe what the region visibly contains or where it sits",
    ),
}


EVAL_CONTEXT_PREAMBLE = (
    "## Evaluator context — the block immediately below is the rubric the "
    "upstream evaluator used to judge this input. Treat it as background — "
    "it is NOT instructing you to evaluate. The ``{{key}}`` placeholders "
    "inside it correspond to inputs supplied above; values have been "
    "substituted in-line."
)


_TASK_TMPL = """## Error localization task

<Evaluation Name>
{{eval_name}}
</Evaluation Name>

<Evaluation Result>
{{evaluation_result}}
</Evaluation Result>

<Evaluation Explanation (the evaluator's reason — your authoritative source)>
{{evaluation_explanation}}
</Evaluation Explanation>{{choices_line}}

<Selected Input>
{{selected_input_key}}
</Selected Input>

The selected input has been split into {unit_label_plural}, shown as
<{unit_prefix}_N>...</{unit_prefix}_N> blocks{location}. Identify which
units contributed to the failure verdict.

Rules:
- Return at least one entry. If the failure is whole-input, return one
  entry with unit_key="{whole_key}" and the evaluation explanation
  restated as the reason.
- unit_key must be the bare key (e.g. {unit_prefix}_2) — no <, >, /.

Output ONLY a JSON array. Each entry has:
- "unit_key": the bare unit key.
- "rank": a unique integer string starting at "1" (most severe first).
- "reason": one or two sentences in plain end-user language. {describe}.
- "improvement": a concrete suggestion for fixing this region, in the
  same plain language.
- "rank_reason": one sentence on why this entry is ranked here.

Example:
[
  {{{{"unit_key": "{unit_prefix}_3", "rank": "1", "reason": "{example_reason}", "improvement": "{example_improvement}", "rank_reason": "{example_rank_reason}"}}}}
]

Wrap the final array exactly like:

<Error Localization Result>
[ ... your JSON array ... ]
</Error Localization Result>

Return the wrapped array now, with no leading or trailing prose."""

LOCALIZER_TASK = {
    "text": _TASK_TMPL.format(
        unit_label_plural="sentences",
        unit_prefix="sentence",
        whole_key="whole_text",
        location=" appended after this block",
        describe=(
            "Describe the visible content or position (e.g. 'the opening "
            "sentence states ...', 'the final clause claims that ...', "
            "'across the entire response')"
        ),
        example_reason=(
            "The opening sentence states the order was delivered, "
            "contradicting the <expected> answer that it has not been "
            "delivered."
        ),
        example_improvement=(
            "Rephrase the opening to acknowledge the order is still in "
            "transit rather than asserting it was delivered."
        ),
        example_rank_reason=(
            "This is the most severe error because it directly contradicts "
            "the ground-truth answer."
        ),
    ),
    "audio": _TASK_TMPL.format(
        unit_label_plural="time segments",
        unit_prefix="segment",
        whole_key="whole_audio",
        location=" in the attached media (each segment is a text tag + audio block + closing tag)",
        describe=(
            "Describe the audio by its timestamp range (e.g. 'around "
            "12s-18s, the speaker mentions ...', 'in the final few seconds "
            "the tone shifts to ...', 'throughout the recording')"
        ),
        example_reason=(
            "Around 8s-12s the speaker uses a sarcastic tone, contradicting "
            "the criterion that the response be sincere."
        ),
        example_improvement="Re-record this portion in a neutral, sincere tone.",
        example_rank_reason=(
            "This segment carries the clearest signal of the sentiment "
            "violation flagged by the evaluator."
        ),
    ),
    "image": _TASK_TMPL.format(
        unit_label_plural="patches",
        unit_prefix="patch",
        whole_key="whole_image",
        location=(
            " in the attached media — a full-image thumbnail is shown first "
            "for global context, followed by up to 20 zoomed-in patches "
            "with positional metadata (top-left / center / etc.)"
        ),
        describe=(
            "Describe the region by what it visibly contains (e.g. 'the "
            "character's face in the upper-right region', 'the left sleeve "
            "of the tracksuit', 'the bookshelves in the background')"
        ),
        example_reason=(
            "The left sleeve of the tracksuit is white, contradicting the "
            "<prompt> request for an all-black tracksuit."
        ),
        example_improvement=(
            "Re-render the sleeve in matching black so the tracksuit is "
            "uniformly the colour specified by the prompt."
        ),
        example_rank_reason=(
            "This is the most severe error because the colour mismatch is "
            "the central detail the prompt asked for."
        ),
    ),
}
