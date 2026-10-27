CLUSTER_PATTERN_PROMPT = """You are analyzing a cluster of evaluation explanations.
Each item explains *why a specific output/input* was judged (e.g., toxicity, hallucination, context adherence, audio quality, synthetic image detection, etc.).

Your task is to find common patterns in the *underlying content/behavior being evaluated* — not to critique the rubric, evaluator, dataset, or process.

Scope & constraints:
- Focus strictly on recurring issues in the content/outputs (e.g., targeted slurs toward group X, missing citations for entity Y, failure to include required field Z, mic hiss at high sibilants, GAN-like skin texture, etc.).
- Assume the explanations are correct. Do NOT comment on evaluation design, criteria, weighting, methodology, or “how to evaluate better.”
- Prefer one primary theme; add optional subthemes only if clearly supported.

Return JSON only with keys:
{{
  "theme": "<short alert describing the dominant recurring issue in the content>",
  "triggers": ["<short trigger/condition 1>", "<short trigger/condition 2>"],
  "evidence_summary": "<1-2 sentences grounded in the representative examples (no meta-critique).>",
  "guidance": "<action to reduce recurrence>",
  "confidence": "<low|medium|high>"
}}

Notes:
- "triggers" are compact surface cues or conditions (keywords, contexts, acoustic cues, image artifacts, etc.) that repeatedly co-occur with the failure.
- "guidance" must be actionable data/model/process changes (e.g., augment with counterexamples for group X; add retrieval constraint for entity Y; add ASR denoising for sibilants; tighten face-skin texture detector).
- Only highlight negative patterns; do not include positive examples.
- If no strong theme is present, set:
  "theme": "No dominant theme detected",
  "triggers": [],
  "evidence_summary": "No clear recurrence across examples.",
  "guidance": "",
  "confidence": "low"



Example 1 (Toxicity evaluation):
{{
  "theme": "Toxicity targeting women",
  "triggers": ["Derogatory terms for women", "Associating female references with insults"],
  "evidence_summary": "Several evaluated outputs contain direct insults toward women, often using gendered slurs or negative stereotypes.",
  "guidance": "Augment training data with positive and neutral references to women, and strengthen filtering of gender-based slurs.",
  "confidence": "high"
}}

Example 2 (Hallucination detection):
{{
  "theme": "Fabricated historical dates",
  "triggers": ["Specific year with unsupported claim", "Confident but incorrect historical reference"],
  "evidence_summary": "Multiple outputs assert historical dates for events that did not occur or are contradicted by authoritative sources.",
  "guidance": "Integrate fact-checking against trusted historical databases and penalize unsupported temporal claims during training.",
  "confidence": "medium"
}}
Cluster size: {size}
Representative explanations:
<explanations>
{samples}
</explanations>


Do not start with ```json and end with ``` just return the required json structure.
"""

ANALYSIS_PROMPT = """You are an expert Data Analyst reviewing evaluation explanations for a single quality metric of one conversational AI agent.

Context:
- Each explanation is written by an evaluator to justify the score for ONE metric (e.g., audio quality, completeness, empathy) on a single call scenario.
- All explanations in this block come from stress tests of the SAME agent across many scenarios and user personas.
- The metric being evaluated is: {dimension_name}

Your task is to read the following list of evaluation explanations and extract distinct, factual patterns in the agent's behaviour and the overall call experience.

Explanations:
{samples}

Instructions:
1. Read each explanation carefully.
2. Identify specific keywords, error messages, or behavioural patterns that appear multiple times across scenarios.
3. Ignore one-off anomalies; focus on issues or behaviours that repeat (at least 2-3 occurrences).
4. List 3-6 distinct "Behavioural Patterns" you observe.
5. For each pattern, specify if it is a "FAILURE" (defect, risk, negative user impact) or "SUCCESS" (strength, positive behaviour, good user impact).
6. Provide 2-3 exact quotes or substrings from the text as evidence.
7. Do NOT critique the evaluation rubric or scoring method; only describe the agent behaviour and call experience being evaluated.
8. For every pattern, ALWAYS include "(Type: FAILURE)" or "(Type: SUCCESS)" exactly in the pattern header line.

Output Format (Text):
- Pattern 1: [Name of the pattern] (Type: FAILURE/SUCCESS)
  - Evidence: "[Quote 1]", "[Quote 2]"
- Pattern 2: [Name of the pattern] (Type: FAILURE/SUCCESS)
  - Evidence: "..."
...
"""

SYNTHESIS_PROMPT = """You are a Product Strategist.
You are reviewing behavioural patterns for ONE evaluation metric of a single conversational AI agent.

Analysis (Patterns with FAILURE/SUCCESS labels):
{analysis}

Instructions:
1. Group related patterns into a coherent story for this metric.
2. Determine if this cluster is primarily about FAILURES or SUCCESSES:
   - If ONLY SUCCESS patterns exist with no meaningful failures → treat as SUCCESS cluster
   - If ANY recurring FAILURE patterns exist → treat as FAILURE cluster
3. For FAILURE clusters:
   - Theme: Concise title describing the dominant failure pattern
   - Issues: List 2-4 specific recurring problems (what goes wrong)
   - User Impact: How these failures affect end-user experience
   - System Logic / Root Cause: What the agent is doing (or failing to do) that causes these issues
   - Recommendation: 1-3 concrete, actionable fixes (changes to prompts, training, routing, audio/infra)
4. For SUCCESS clusters:
   - Theme: Concise title describing the dominant strength
   - Capabilities: List 2-4 specific positive behaviours the agent demonstrates consistently
   - User Impact: How these strengths enhance end-user experience
   - System Logic: What the agent is doing well
   - Recommendation: How to preserve/amplify these strengths or apply them to other areas

Output Format for FAILURE clusters (Text):
Type: FAILURE
Theme: [Short title capturing the dominant failure]
Issues:
- [Issue 1]
- [Issue 2]
User Impact: [Short paragraph]
System Logic: [Short paragraph]
Recommendation: [Concrete actions to fix]

Output Format for SUCCESS clusters (Text):
Type: SUCCESS
Theme: [Short title capturing the dominant strength]
Capabilities:
- [Capability 1]
- [Capability 2]
User Impact: [Short paragraph]
System Logic: [Short paragraph]
Recommendation: [How to preserve/amplify]
"""

CRITIC_PROMPT = """You are a Strict QA Critic.
Review the Draft Summary against the original Representative Examples.
Your goal is to validate accuracy and format the final output as JSON.

Draft Summary:
{draft}

Representative Examples:
{samples}

Instructions:
1. Determine if this is a FAILURE or SUCCESS cluster based on the Draft Summary's "Type" field.
2. Check if the content accurately reflects the examples.
3. Return 'status': 'accepted' if consistent with examples, else 'status': 'rejected'.
4. In "evidence_summary", state behavioural facts directly without meta-commentary (avoid phrases like "The draft summary says..." or "The examples show...").
5. "guidance" must be concrete and actionable - specify exact changes or implementation details.
6. Format output as valid JSON - return ONLY the JSON structure matching the cluster type.
7. **CRITICAL:** Do NOT include the key "issues" in SUCCESS clusters and Do NOT include the key "capabilities" in FAILURE clusters.

FOR FAILURE CLUSTERS, return:
{{
  "theme": "<short title describing the failure>",
  "status": "<accepted|rejected>",
  "issues": ["<specific issue 1>", "<specific issue 2>"],
  "triggers": ["<specific trigger 1>", "<specific trigger 2>"],
  "evidence_summary": "<concise summary of the recurring failure behaviour>",
  "guidance": "<actionable advice to fix the issues>",
  "confidence": "<low|medium|high>"
}}

FOR SUCCESS CLUSTERS, return:
{{
  "theme": "<short title describing the strength>",
  "status": "<accepted|rejected>",
  "capabilities": ["<specific capability 1>", "<specific capability 2>"],
  "application_contexts": ["<where this strength appears 1>", "<where this strength appears 2>"],
  "evidence_summary": "<concise summary of the recurring positive behaviour>",
  "guidance": "<actionable advice to preserve or amplify this strength>",
  "confidence": "<low|medium|high>"
}}

Do not start with ```json and end with ``` just return the required json structure.
"""