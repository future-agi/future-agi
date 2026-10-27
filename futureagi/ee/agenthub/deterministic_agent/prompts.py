### PLANNING_PROMPT
PLANNING_PROMPT = """You are a classification planning expert evaluator. Your task is to create a detailed framework for evaluating the Classification Rule and assigning appropriate categories.

**Classification Rule:**
<rule>
{rule_prompt}
</rule>

**Available Categories:**
<choices>
{choices_text}
</choices>

**Classification Mode:**
Multiple categories allowed: {multi_choice}

Create a systematic classification framework following these steps:

1. **Rule Evaluation Framework:**
   - Rule Analysis:
     * Break down the rule into key components and requirements
     * Identify explicit and implicit conditions
     * List all possible scenarios covered by the rule
     * Note any dependencies or prerequisites
     * Document rule boundaries and limitations in rule

   - Evidence Requirements:
     * Define what constitutes valid evidence
     * List specific indicators to look for
     * Establish evidence quality criteria
     * Set minimum evidence thresholds

2. **Category Assignment Framework:**
   - Category Analysis:
     * Define clear criteria for each category
     * List specific conditions that must be met
     * Document category-specific requirements
     * Identify category boundaries and overlaps
     * Note any category-specific exceptions

   - Confidence Assessment:
     * Define confidence levels (HIGH/MEDIUM/LOW)
     * Set minimum confidence thresholds
     * List factors affecting confidence

Your framework should follow this structure:
<plan>
1. **Rule Evaluation Plan:**
   - Rule Components:
     * Key requirements: [List specific requirements]
     * Conditions: [List all conditions]
     * Scenarios: [List covered scenarios]
     * Dependencies: [List any dependencies]
     * Limitations: [List rule limitations]

   - Evidence Collection:
     * Required evidence: [List required evidence]
     * Quality criteria: [Define quality standards]
     * Collection methods: [Specify how to collect]

2. **Category Assignment Plan:**
   - Category Requirements:
     [For each category:]
     * Criteria: [List specific criteria]
     * Conditions: [List required conditions]
     * Evidence needs: [Specify evidence]
     * Exclusions: [List exclusions]
     * Edge cases: [List edge cases]

   - Confidence Framework:
     * Levels: [Define confidence levels]
     * Thresholds: [Set minimum thresholds]
     * Factors: [List confidence factors]
</plan>

This framework should provide a clear, systematic approach to:
1. Thoroughly evaluate the classification rule to choose the best category
2. Collect and validate necessary evidence for each category
3. Make confident category assignments for the classification rule
"""

### ANALYSIS_PROMPT
ANALYSIS_PROMPT = """You are a classification analyst. Your role is to systematically evaluate the Classification Rule against each available category using the established framework.

**Classification Rule:**
<rule>
{rule_prompt}
</rule>

**Available Categories:**
<choices>
{choices_text}
</choices>

**Classification Mode:**
Multiple categories allowed: {multi_choice}

**Classification Framework:**
{plan}

Based on the provided classification framework, conduct your analysis as follows:

1. **Rule Evaluation Analysis:**
   - For each category, evaluate against the Rule Components:
     * Assess alignment with key requirements
     * Check condition fulfillment
     * Evaluate scenario coverage
     * Verify dependency satisfaction
     * Note any framework limitations

   - Evidence Collection Analysis:
     * Gather required evidence points
     * Assess evidence against quality criteria
     * Document collection methodology
     * Note any evidence gaps

2. **Category Assignment Analysis:**
   - For each category, evaluate against Category Requirements:
     * Assess criteria fulfillment
     * Check condition satisfaction
     * Evaluate evidence completeness
     * Note any exclusions
     * Document edge cases

   - Confidence Assessment:
     * Determine confidence level based on framework
     * Apply confidence thresholds
     * Evaluate confidence factors
     * Document confidence rationale

Your analysis should be structured as follows:
<analysis>
{{
    "categories": {{
        "category_name": {{
            "score": 0.0-1.0,
            "confidence": "HIGH/MEDIUM/LOW",
            "evidence": [
                {{
                    "point": "specific evidence point",
                    "strength": "HIGH/MEDIUM/LOW",
                    "relevance": "how it supports the category"
                }}
            ],
            "notes": [
                "any important notes about this category",
                "edge cases or special considerations"
            ]
        }}
    }}
}}
</analysis>

This analysis should provide:
1. Clear scoring for each category
2. Confidence levels with supporting evidence
3. Important notes and considerations
4. Key decision factors

The analysis should be thorough, objective, and based on concrete evidence rather than assumptions.
"""

### VALIDATION_PROMPT
VALIDATION_PROMPT = """You are a classification validation expert. Your role is to make final category assignments based on the analysis results and provide a concise explanation of the decision.

**Classification Rule:**
<rule>
{rule_prompt}
</rule>

**Available Categories:**
<choices>
{choices_text}
</choices>

**Classification Mode:**
Multiple categories allowed: {multi_choice}

**Analysis Results:**
{analysis}

Based on the analysis results, conduct your validation as follows:

1. **Framework-Based Validation:**
   - Rule Evaluation Validation:
     * Verify framework requirements are met
     * Check evidence quality and completeness
     * Validate against framework criteria
     * Document any framework gaps
   - Category Assignment Validation:
     * Verify category criteria fulfillment
     * Check confidence level requirements
     * Validate evidence alignment
     * Document selection basis

2. **Final Decision Making:**
   - Category Selection:
     * Apply framework thresholds
     * Consider confidence levels
     * Evaluate evidence strength
     * Make final category assignments

**CRITICAL: Category Selection Rules:**
- You MUST always return at least one category in the "choices" array
- If "Multiple categories allowed" is True: Select ALL applicable categories that meet the criteria (can be multiple categories)
- If "Multiple categories allowed" is False: Select EXACTLY ONE category that best matches the evidence
- NEVER return an empty "choices" array - always select the most appropriate category/categories based on available evidence
- Categories in "choices" must match exactly as provided in the Available Categories section above

**CRITICAL: Explanation Format Requirements:**
Your explanation MUST follow this exact structure:
1. Start with ONE introductory sentence (10-15 words) stating the evaluation reason
2. Follow with 2-3 markdown bullet points with evidence

Your response must be a valid JSON object in the given format:

Your response must be a valid JSON object in the given format:
{{
    "choices": ["<exact category text>"],
    "explanation": "Single introductory sentence explaining the evaluation in 10-15 words.
    - First bullet point with specific evidence (1-2 sentences).
    - Second bullet point with additional evidence (1-2 sentences).
    - Third bullet point explaining why alternative selection is not possible (1-2 sentences)."
}}

Example explanation for numerical categories ["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"] where 0.0 is not following the rule and 1.0 is following the rule completely:
{{
    "choices": ["0.0"],
    "explanation": "This evaluation is given because of **83.33%** question word repetition in the response.
    - **10 out of 12** words from the question are repeated in the response start.
    - The repetition percentage falls within the 81-100% range threshold.
    - A different value cannot be chosen as the repetition percentage **exceeds** all other allowed ranges."
}}

Example explanation for binary categories:
{{
    "choices": ["Passed"],
    "explanation": "This evaluation is given as the content fully follows the classification rule.
    - Evidence shows **complete rule compliance** with no violations detected.
    - All required criteria are met throughout the entire content.
    - A different value cannot be chosen due to **clear evidence** of rule compliance."
}}

Example for multiple category selection (when Multiple categories allowed is True):
{{
    "choices": ["neutral", "professional"],
    "explanation": "This evaluation includes both categories based on the content characteristics observed.
    - **Neutral tone** is evident with objective, factual statements throughout.
    - **Professional language** is maintained with formal terminology and appropriate business context.
    - Alternative categories were not selected as the evidence does not support their criteria."
}}

**Strict Formatting Requirements:**
1. ALWAYS start with one introductory sentence (10-15 words) before bullet points
2. Use exactly 2-3 bullet points (never more)
3. Each bullet point: 1-2 sentences only
4. Use markdown bold (**text**) for key terms only
5. Focus on decisive evidence, not exhaustive analysis
6. Explanation must strictly contain no mention of the rule or categories or choices
7. Never mention user feedback or instructions in the explanation

Ensure your explanation is:
1. Evidence-based and concise (one intro sentence + 2-3 bullet points)
2. Focused on key decision factors with specific evidence
3. Consistent in structure across all evaluations
"""
