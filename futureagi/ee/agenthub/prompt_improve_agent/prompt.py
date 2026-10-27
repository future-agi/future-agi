ANALYSIS_PROMPT = """You are a prompt analysis expert. Analyze the following prompt and improvement requirements:

Original Prompt:
{original_prompt}

Improvement Requirements:
{improvement_requirements}

Analyze the following aspects:
1. Current prompt structure and variables
2. Key elements that need to be preserved
3. Areas that need improvement according to the requirements
4. Potential conflicts between improvements and existing structure if any
5. Variables and placeholders that must be maintained

Provide your analysis in a structured format and in a logical manner.

"""

IMPROVEMENT_PROMPT = """You are a prompt improvement expert. Based on the analysis, improve the following prompt while maintaining its core structure:

Original Prompt:
{original_prompt}

Improvement Requirements:
{improvement_requirements}

Analysis Results:
{analysis}

Requirements:
1. Maintain all existing variables and placeholders and their syntax and structure
2. Implement the requested improvements by using  analysis results
3. Preserve any special formatting or syntax

NOTE: DONT ADD ANY EXTRA VARIABLES OR PLACEHOLDERS FROM THE ORIGINAL PROMPT

Return your response in the RFC8259 compliant JSON format only :

{{
"improved_prompt": "The improved prompt text",
"explanation": "Brief explanation of changes made"
}}

"""


FOLLOWUP_PROMPT = """You are a prompt synthesis expert. Analyze both the original and improved prompts, then create a final version that combines the best elements of both while maintaining all required variables and structure.

Original Prompt:
{original_prompt}

Improved Prompt:
{improved_prompt}

Improvement Requirements:
{improvement_requirements}

Requirements:
1. Preserve all variables and placeholders from both versions
2. Maintain compatibility with existing systems
3. Combine the strengths of both prompts
4. Ensure clarity and effectiveness
5. Keep the core functionality intact
6. Ensure that the final prompt is fulfilling the improvement requirements

Return your response in the RFC8259 compliant JSON format only :
{{
    "final_prompt": "The synthesized prompt combining best elements of both versions",
    "explanation": "Brief explanation of how elements were combined and why"
}}

"""
