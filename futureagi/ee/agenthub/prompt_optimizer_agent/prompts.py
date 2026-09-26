GLOSSARY_TEXT_BACKWARD = """
### Glossary of tags that will be sent to you:
# - <LM_INPUT>: The input to the language model.
# - <LM_OUTPUT>: The output of the language model."""

BACKWARD_SYSTEM_OUTPUT_FORMAT = """
### FEEDBACK :
# - <FEEDBACK 1>: The output of the language model.
# ...
# ...
# ...
# <FEEDBACK N>: The output of the language model """

# Backward engine prompts

# System prompt to the backward engine.
BACKWARD_SYSTEM_PROMPT = (
    "You are part of an optimization system that improves a given text (i.e. the variable). You are the gradient (feedback) engine. "
    "Your only responsibility is to give intelligent and creative feedback and constructive criticism to variables, given an objective specified in <OBJECTIVE_FUNCTION> </OBJECTIVE_FUNCTION> tags. "
    "The variables may be solutions to problems, prompts to language models, code, or any other text-based variable. "
    "Pay attention to the role description of the variable, and the context in which it is used. You should assume that the variable will be used in a similar context in the future. "
    "Only provide strategies, explanations, and methods to change in the variable. DO NOT propose a new version of the variable, that will be the job of the optimizer. Your only job is to send feedback and criticism (compute 'gradients'). "
    "For instance, feedback can be in the form of 'Since language models have the X failure mode...', 'Adding X can fix this error because...', 'Removing X can improve the objective function because...', 'Changing X to Y would fix the mistake ...', that gets at the downstream objective.\n"
    "If a variable is already working well (e.g. the objective function is perfect, an evaluation shows the response is accurate), you should not give feedback.\n"
    f"{GLOSSARY_TEXT_BACKWARD}"
    f"Provide the output in following format {BACKWARD_SYSTEM_OUTPUT_FORMAT} "
)

# First part of the prompt for the llm backward function
CONVERSATION_TEMPLATE = (
    "<LM_INPUT> {prompt} </LM_INPUT>\n\n" "<LM_OUTPUT> {llm_response} </LM_OUTPUT>\n\n"
)

FEEDBACK_PROMPT = BACKWARD_SYSTEM_PROMPT + "\n\n\n" + CONVERSATION_TEMPLATE

GLOSSARY_TEXT = """
### Glossary of tags that will be sent to you:
# - <LM_INPUT>: The input to the language model.
# - <LM_OUTPUT>: The output of the language model.
# - <SCORE>: Score generated for the LM OUTPUT based on a scale of 1 to 5 where 1 is low quality and 5 is good quality.
# - <CRITERIA_JUDGEMENT>: List of criteria used for calculating score and judgement for each criteria.
# - <IMPROVEMENTS>: The improvements required for <LM_INPUT> after incorporating <CRITERIA_JUDGEMENT> to increase <SCORE> """

GLOSSARY_TEXT_V2 = """
### Glossary of tags that will be sent to you:
# - <INSTRUCTION_TEMPLATE>: The template describing the instruction followed by the language model after plugging in variables.
# - <INSTRUCTION>: The input to the language model.
# - <SCORE>: Score generated for the LM OUTPUT based on a scale of 1 to 5 where 1 is low quality and 5 is good quality.
# - <CRITERIA_JUDGEMENT>: List of criteria used for calculating score and judgement for each criteria.
# - <IMPROVEMENTS>: The improvements required for <LM_INPUT> after incorporating <CRITERIA_JUDGEMENT> to increase <SCORE> """

GLOSSARY_TEXT_V2_VAR = """
### Glossary of tags that will be sent to you:
# - <INSTRUCTION_TEMPLATE>: The template describing the instruction followed by the language model after plugging in variables. Example: Describe $concept$ \n, here $concept$ is variable.
# - <INSTRUCTION>: The input to the language model.
# - <SCORE>: Score generated for the LM OUTPUT based on a scale of 1 to 5 where 1 is low quality and 5 is good quality.
# - <CRITERIA_JUDGEMENT>: List of criteria used for calculating score and judgement for each criteria.
# - <IMPROVEMENTS>: The improvements required for <LM_INPUT> after incorporating <CRITERIA_JUDGEMENT> to increase <SCORE> """

GLOSSARY_TEXT_V3 = """
### Glossary of tags that will be sent to you:
# - <INSTRUCTION_TEMPLATE>: The template describing the instruction followed by the language model after plugging in variables.
# - <INSTRUCTION>: The input to the language model after plugging variables in the <INSTRUCTION_TEMPLATE>
# - <INSTRUCTION_IMPROVEMENTS>: Score generated for the LM OUTPUT based on a scale of 1 to 5 where 1 is low quality and 5 is good quality."""

GLOSSARY_TEXT_V4 = """
### Glossary of tags that will be sent to you:
# - <INSTRUCTION_TEMPLATE>: The template describing the instruction followed by the language model after plugging in variables."""

OPTIMIZER_OUTPUT_FORMAT = """
# - <LM_INPUT>: The input to the language model.
# - <IMPROVED_LM_INPUT>: The improved version of <LM_INPUT> after incorporating feedback. """


# System prompt to TGD
OPTIMIZER_SYSTEM_PROMPT = (
    "You are part of an optimization system that improves text (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive some feedback, and use the feedback to improve the variable enclosed in <LM_INPUT>. "
    "The feedback may be noisy, identify what is important and what is correct. "
    "Pay attention to the role description of the variable, and the context in which it is used. "
    f"{GLOSSARY_TEXT}"
    f"Please provide output in following format {OPTIMIZER_OUTPUT_FORMAT} \n\n\n"
)

FEEDBACK_TEMPLATE = (
    "<LM_INPUT> {prompt} </LM_INPUT>\n\n"
    "<LM_OUTPUT> {llm_response} </LM_OUTPUT>\n\n"
    "<FEEDBACK> {loss} </FEEDBACK>\n\n"
)

OPTIMIZER_PROMPT = OPTIMIZER_SYSTEM_PROMPT + "\n\n\n" + FEEDBACK_TEMPLATE


FEEDBACK_TEMPLATE = (
    "<LM_INPUT> {prompt} </LM_INPUT>\n\n"
    "<LM_OUTPUT> {llm_response} </LM_OUTPUT>\n\n"
    "<SCORE> {score} </SCORE>\n\n"
    "<CRITERIA_JUDGEMENT> {CRITERIA_JUDGEMENT} </FEEDBACK>\n\n"
)

FEEDBACK_TEMPLATE_V2 = (
    "<INSTRUCTION_TEMPLATE> {system_prompt} </INSTRUCTION_TEMPLATE>\n\n"
    "<INSTRUCTION> {prompt} </INSTRUCTION>\n\n"
    "<SCORE> {score} </SCORE>\n\n"
    "<CRITERIA_JUDGEMENT> {CRITERIA_JUDGEMENT} </CRITERIA_JUDGEMENT>\n\n"
)

FEEDBACK_TEMPLATE_MB = (
    "<INSTRUCTION_TEMPLATE> {system_prompt} </INSTRUCTION_TEMPLATE>\n\n"
    "<SCORE> {score} </SCORE>\n\n"
    "<CRITERIA_JUDGEMENT> {CRITERIA_JUDGEMENT} </CRITERIA_JUDGEMENT>\n\n"
)

IMPROVED_INSTRUCTION_INPUT_TEMPLATE = (
    "<INSTRUCTION_TEMPLATE> {system_prompt} </INSTRUCTION_TEMPLATE>\n\n"
    "<INSTRUCTION> {prompt} </INSTRUCTION>\n\n"
    "<INSTRUCTION_IMPROVEMENTS> {improved_instructions} </INSTRUCTION_IMPROVEMENTS>\n\n"
)

IMPROVED_INSTRUCTION_INPUT_TEMPLATE_MB = (
    "<INSTRUCTION_TEMPLATE> {system_prompt} </INSTRUCTION_TEMPLATE>\n\n"
    "<INSTRUCTION_IMPROVEMENTS> {improved_instructions} </INSTRUCTION_IMPROVEMENTS>\n\n"
)

OPTIMIZER_OUTPUT_FORMAT_V2 = """
# - <INSTRUCTION_IMPROVEMENTS>: The improvements for <INSTRUCTION_TEMPLATE> after incorporating criteria and judgements </NSTRUCTION_IMPROVEMENTS> """

IMPROVED_INSTRUCTION_OUTPUT_FORMAT = """
# - <IMPROVED_INSTRUCTION_TEMPLATE>: The improved  <INSTRUCTION_TEMPLATE> after incorporating improved instructions provided. </IMPROVED_INSTRUCTION_TEMPLATE> """


IMPROVED_INSTRUCTION_OUTPUT_FORMAT_MB = (
    """ The improved  INSTRUCTION_TEMPLATE after incorporating improvements provided."""
)

IMPROVED_INSTRUCTION_SYSTEM_PROMPT_MB = (
    "You are part of an optimization system that improves text-based variables (e.g., prompts, solutions, code). "
    "You will be provided with <INSTRUCTION_TEMPLATE>, <INSTRUCTION>, and <INSTRUCTION_IMPROVEMENTS>. "
    "Use <INSTRUCTION_IMPROVEMENTS> to creatively and critically enhance the variable enclosed in <INSTRUCTION_TEMPLATE> "
    "to address the task mentioned in <INSTRUCTION>. "
    "The <INSTRUCTION_IMPROVEMENTS> may contain noise and repetition. Focus on identifying and incorporating the important and correct aspects. "
    f"{GLOSSARY_TEXT_V3}"
    f"\n\n\n Please provide output in str format . Keep only the output text of <> and dont add any identifier in string format. Do not add new lines or other escape characters unnecessarily. \n\n\n"
)

# System prompt to TGD
CRITERIA_CORRECTOR_SYSTEM_PROMPT = (
    "You are part of an optimization system that improves text (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive <INSTRUCTION_TEMPLATE>,<INSTRUCTION>,<SCORE>,<CRITERIA> and <JUDGEMENT> . Use the criteria and judgement pair to suggest improvements for the variable enclosed in <INSTRUCTION_TEMPLATE> to increase the score FOR <INSTRUCTION>"
    "The criteria and judgement pair may be noisy, identify what is important and what is correct. "
    "Pay attention to the task mentioned in <INSTRUCTION_TEMPLATE>"
    "Please suggest only generic improvements which do not include anything specifics from instruction"
    "Please do not provide improved instruction template"
    f"{GLOSSARY_TEXT_V2}"
    f"\n\n\n Please provide output in following format {OPTIMIZER_OUTPUT_FORMAT_V2} \n\n\n"
)

# System prompt to TGD
CRITERIA_CORRECTOR_SYSTEM_PROMPT_MB = (
    "You are part of an optimization system that improves text (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive <INSTRUCTION_TEMPLATE>,<SCORE>,<CRITERIA> and <JUDGEMENT> . Use the list of criteria and judgement pairs to suggest improvements for the variable enclosed in <INSTRUCTION_TEMPLATE> to increase the score."
    "The criteria and judgement pair may be noisy, identify what is important and what is correct. "
    "Pay attention to the task mentioned in <INSTRUCTION_TEMPLATE>"
    "Please suggest only generic improvements which do not include anything specifics from instruction"
    "Please do not provide improved instruction template"
    f"{GLOSSARY_TEXT_V2}"
    f"\n\n\n Please provide output in following format {OPTIMIZER_OUTPUT_FORMAT_V2} \n\n\n"
)

CRITERIA_CORRECTOR_SYSTEM_PROMPT_VAR = (
    "You are part of an optimization system that improves text (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive <INSTRUCTION_TEMPLATE>,<SCORE>,<CRITERIA> and <JUDGEMENT> . Use the list of criteria and judgement pairs to suggest improvements for the variable enclosed in <INSTRUCTION_TEMPLATE> to increase the score."
    "The criteria and judgement pair may be noisy, identify what is important and what is correct. "
    "Pay attention to the task mentioned in <INSTRUCTION_TEMPLATE> and dont pay attention to variables enclosed in $$"
    "Please suggest only generic improvements which do not include anything specifics from instruction"
    "Please do not suggest improvements by paying attention to variables"
    "Please do not provide improved instruction template"
    f"{GLOSSARY_TEXT_V2_VAR}"
    f"\n\n\n Please provide output in following format {OPTIMIZER_OUTPUT_FORMAT_V2} \n\n\n"
)

CRITERIA_CORRECTOR_PROMPT = (
    CRITERIA_CORRECTOR_SYSTEM_PROMPT + "\n\n\n" + FEEDBACK_TEMPLATE_V2
)

CRITERIA_CORRECTOR_PROMPT_VAR = (
    CRITERIA_CORRECTOR_SYSTEM_PROMPT_VAR + "\n\n\n" + FEEDBACK_TEMPLATE_MB
)


IMPROVED_INSTRUCTION_SYSTEM_PROMPT = (
    "You are part of an optimization system that improves text (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive <INSTRUCTION_TEMPLATE>,<INSTRUCTION>,<INSTRUCTION_IMPROVEMENTS>. Use the INSTRUCTION_IMPROVEMENTS  to improve the variable enclosed in <INSTRUCTION_TEMPLATE> to increase the score FOR <INSTRUCTION>"
    "The <INSTRUCTION_IMPROVEMENTS> may be noisy, repetitive . Identify what is important and what is correct. "
    "Pay attention to the task mentioned in <INSTRUCTION_TEMPLATE>"
    f"{GLOSSARY_TEXT_V3}"
    f"\n\n\n Please provide output in following format {IMPROVED_INSTRUCTION_OUTPUT_FORMAT} \n\n\n"
)

INSTRUCTION_CORRECTOR_PROMPT = (
    IMPROVED_INSTRUCTION_SYSTEM_PROMPT + "\n\n\n" + IMPROVED_INSTRUCTION_INPUT_TEMPLATE
)

INSTRUCTION_CORRECTOR_PROMPT_MB = (
    IMPROVED_INSTRUCTION_SYSTEM_PROMPT_MB
    + "\n\n\n"
    + IMPROVED_INSTRUCTION_INPUT_TEMPLATE_MB
)


INSTRUCTION_TEMPLATE_SUMMARIZER_INPUT_TEMPLATE = "<INSTRUCTION_TEMPLATE_LIST> {instruction_template_list} </INSTRUCTION_TEMPLATE_LIST>\n\n"

INSTRUCTION_TEMPLATE_SUMMARIZER_OUTPUT_FORMAT = """
# The improved  <INSTRUCTION_TEMPLATE> after incorporating improved instructions provided """


INSTRUCTION_TEMPLATE_SUMMARIZER = (
    f"{GLOSSARY_TEXT_V4}"
    "You are part of an optimization system that improves INSTRUCTION TEMPLATE (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive list of instruction_templates enclosed in <INSTRUCTION_TEMPLATE> ..... </INSTRUCTION_TEMPLATE> "
    "Pay attention to the points mentioned in every <INSTRUCTION_TEMPLATE> and generate a <INSTRUCTION_TEMPLATE> which covers all points mentioned in given templates."
    "Do not make the <IMPROVED_INSTRUCTION_TEMPLATE> inclined towards a particular instruction_template. Keep it concise, generic and cover all points in the above templates."
    "You will return the IMPROVED_INSTRUCTION_TEMPLATE response as a string . Dont include any unrequired escape-characters,formats or <> or new lines "
)

INSTRUCTION_TEMPLATE_SUMMARIZER_VAR = (
    f"{GLOSSARY_TEXT_V4}"
    "You are part of an optimization system that improves INSTRUCTION TEMPLATE (i.e., variable). "
    "You will be asked to creatively and critically improve prompts, solutions to problems, code, or any other text-based variable. "
    "You will receive list of instruction_templates enclosed in <INSTRUCTION_TEMPLATE> ..... </INSTRUCTION_TEMPLATE>  which may or may not contain contain variables enclosed in $$"
    "Pay attention to the points mentioned in every <INSTRUCTION_TEMPLATE> and generate a <INSTRUCTION_TEMPLATE> which covers all points mentioned in given templates."
    "Assume all the instruction templates provided have exact same number and name of variables. Please make sure that the variables used in the input templates is included in the new improved template and no variable is missed out. "
    "Do not make the <IMPROVED_INSTRUCTION_TEMPLATE> inclined towards a particular instruction_template. Keep it concise, generic and cover all points in the above templates."
    "You will return the IMPROVED_INSTRUCTION_TEMPLATE response as a string . Dont include any unrequired escape-characters,formats or <> or new lines "
)

INSTRUCTION_TEMPLATE_SUMMARIZER_VAR_PROMPT = (
    INSTRUCTION_TEMPLATE_SUMMARIZER_VAR
    + "\n\n\n"
    + INSTRUCTION_TEMPLATE_SUMMARIZER_INPUT_TEMPLATE
)

GLOSSARY_CRITERIA_SHORTENER = """
### Glossary of tags that will be sent to you:
# - <METRIC>...</METRIC>: Here metric symbolises the instruction for which criterias were generated.
# - <CRITERIA>: Evaluation condition generated for metric. """

CRITERIA_SHORTENER_INPUT = " {metric_criteria_list}"
CRITERIA_SHORTENER_PROMPT_PREFIX = (
    "Given a list of criterias each generated for a particular metric enclosed in <METRIC> ... </METRIC> , generate a list of criterias that represents criteria from every <METRIC> equally."
    "Restrict the final list of criterias to non-overlapping criterias."
    "You will return the response in a RFC8259 compliant JSON response with the serial number as keys, starting from 1. There should be nothing in the response apart from the JSON"
    f"{GLOSSARY_CRITERIA_SHORTENER}"
)

CRITERIA_SHORTENER_PROMPT = (
    CRITERIA_SHORTENER_PROMPT_PREFIX + "\n" + CRITERIA_SHORTENER_INPUT
)


INSTRUCTION_TEMPLATE_SUMMARIZER_V2_PREFIX = (
    "You are an optimization agent. You will receive an initial prompt, list of description of variables used in the prompt if any and a list of improvements required. Incorporate these improvements into the prompt and generate a better version of the prompt. Focus on improvements to create better version and use all the present variables in the generated improved template. "
    "Note: The prompt can contain variables enclosed in $$. For example, $chat$ where chat is a variable."
    "Format for Input:"
    "Prompt: The prompt"
    "Variables Description : List describing about variables used in prompt template"
    "Improvements: List of improvements"
    "Instructions:"
    "Provide only the improved prompt as a string."
    "Do not include any of the input parameters or the format tags (e.g., <Prompt>, <Improvements>)."
    "Ensure the output is formatted as a single line of text with no additional context."
    "Example:"
    "Input:"
    "Prompt: Generate me the summary of the given $chats$"
    "Variables : chats:chat history between 2 person"
    "Improvements: The summary generated should be concise."
    "Output: Generate a concise summary of the given $chats$."
)

INSTRUCTION_TEMPLATE_SUMMARIZER_V2_INPUT = (
    " Prompt: {prompt}" "Variables: {variables}" "Improvements: {improvements}"
)

INSTRUCTION_TEMPLATE_SUMMARIZER_V2 = (
    INSTRUCTION_TEMPLATE_SUMMARIZER_V2_PREFIX
    + "\n"
    + INSTRUCTION_TEMPLATE_SUMMARIZER_V2_INPUT
)


#NEW PROMPT PATTERN

CRITERIA_CORRECTOR_PROMPT_MB="""
You are an AI feedback analyzer focused on prompt improvement. Your task is to analyze evaluation criteria and judgments to suggest specific, actionable improvements for the prompt template.

Input:
1. Prompt Template: {system_prompt}
2. Evaluation Results: {CRITERIA_JUDGEMENT}

Output Requirements:
- Start with "Suggested Improvements:"
- List specific, actionable improvements that directly address the evaluation feedback
- Focus on changes that will measurably improve the prompt's effectiveness
- Keep suggestions clear and implementable

Remember: Your suggestions should directly connect evaluation criteria to concrete improvements.
"""

INSTRUCTION_CORRECTOR_PROMPT_MB="""
You are an AI prompt optimizer. Your task is to enhance the prompt template by implementing the suggested improvements while maintaining its core functionality.

Input:
1. Original Template: {system_prompt}
2. Improvement Suggestions: {improved_instructions}

Requirements:
1. Preserve all existing:
   - Variables and placeholders
   - Core functionality
   - Essential structure
2. Implement improvements by:
   - Integrating feedback naturally
   - Enhancing clarity and effectiveness
   - Removing redundancy
3. Output format: Plain text prompt only

Important: Never modify or remove existing variables/placeholders. Focus on enhancing the prompt while maintaining its technical requirements.
"""

FOLLOW_UP_PROMPT="""
You are an AI prompt refinement specialist. Your task is to create an optimal version by combining the strengths of both original and suggested prompts.

Input:
1. Original: {original_prompt}
2. Suggested: {suggested_prompt}
3. Evaluation Results: {CRITERIA_JUDGEMENT}

Requirements:
1. Merge improvements while preserving:
   - All variables and placeholders
   - Original functionality
   - Technical requirements
2. Focus on:
   - Enhanced clarity
   - Improved effectiveness
   - Optimal structure
   - Addressing evaluation feedback
3. Output: Clean prompt text only

Critical: Maintain exact technical compatibility with the original prompt (variables, placeholders, format).

Begin your refined prompt below:
"""

INSTRUCTION_TEMPLATE_SUMMARIZER_PROMPT = """
You are an AI assistant tasked with improving instruction templates. Analyze multiple templates and create a single, enhanced version that integrates the best elements from each while preserving the original format, placeholders, and variables.

Input:

INSTRUCTION_TEMPLATE_LIST:
{instruction_template_list}

Instructions:

	1.	Review each template carefully and identify key points and unique elements.
	2.	Combine the relevant points into a single, concise, and well-structured template.
	3.	Ensure the new template is balanced, generic, and incorporates all relevant improvements.
	4.	Retain specific instructions and placeholders .
	5.	Do not favor any template; cover all important aspects without redundancy.

Output:

	•	Provide the improved template as plain text, without any formatting, extra characters, or explanations.
	•	Start with the improved template directly.

NOTE: STRICTLY preserving the original format, placeholders, and variables as same as in the input.

Begin:
"""
