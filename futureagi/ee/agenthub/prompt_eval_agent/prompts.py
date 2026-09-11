system_message = """
            Evaluate the clarity and specificity of a given prompt. Respond with a JSON object containing two keys: 'ambiguous' (boolean) and 'explanation' (string).
            Set 'ambiguous' to true if the prompt is unclear, open to multiple interpretations, or uses ambiguous statements.
            Just verify if given prompt is valid or not.
            Ensure the prompt clearly communicates its intent and does not use ambiguous statement.
            Do not consider missing information as a factor for ambiguity unless it leads to multiple interpretations.
            Set it to false if the prompt is clear, specific, and unambiguous.
            Provide a brief explanation for your assessment in the 'explanation' field.
            Ensure your response is RFC8259 compliant JSON with no additional content.
            Given Prompt : {prompt}
        """


system_message_2 = """
            You are a language model tasked with verifying if a given prompt makes sense and is clear.
            POINT TO CONSIDER:
            "1. Do not consider whether any data or additional information is missing; assume all required context is provided. "
            "2. Evaluate if the prompt is open to multiple interpretations or is of uncertain nature or significance. "
            "3. Ensure the prompt clearly communicates its intent and does not use  ambiguous statement. "
            "4. Just verify if given prompt is valid or not.'
            "5. Dont make it ambiguous if required information is missing in it make it ambiguous only when it create multiple interpretations meanings.
            If the prompt is clear and asks for evaluation or analysis without being open to multiple interpretations, respond with 'False'.
            If the prompt is ambiguous, unclear, or open to multiple interpretations, respond with 'True' and explain why.
            You will return the response in a RFC8259 compliant JSON response. There should be nothing in the response apart from the JSON with two keys only : 'explanation' and 'ambiguous'.
            Given Prompt : {prompt}
"""


system_message_generate_prompts = """
            You are a language model tasked with generating better prompt based on the given input information only.
            POINTS TO CONSIDER: "
            1. Ensure the prompt is clear and straightforward, avoiding any ambiguity. "
            2. Prevent multiple interpretations by being specific and precise in the language used.
            3. Assume all required information is already provided; do not consider missing details and add any extra information.
            4. Confirm that the prompt is relevant and asks a valid question or requests a valid action.
            Given the  prompt, generate a revised prompt that addresses these criteria.
            Return the only revised prompt as a string no extra information and keys in response.
            Given Prompt : {prompt}
"""

system_message_generate_promptss = """
Generate a clear, specific, and unambiguous revised prompt based solely on the given input information, ensuring it is relevant and requests a valid action or asks a valid question. Do not add any extra information beyond what is provided in the original prompt. You may use similar words or reframe the words in the prompt to give it a better meaning.
Return the only revised prompt as a single output with no extra information and keys in response.
Given Prompt : {prompt}
"""


system_message_3 = """
            Evaluate the clarity of a given prompt. Respond with a JSON object containing two keys: 'ambiguous' (boolean) and 'explanation' (string).
            Set 'ambiguous' to true if the  given prompt is not valid else make 'ambiguous' to false.
            Do not consider missing information as a factor for ambiguity unless given prompt is not valid.
            Provide a brief explanation for your assessment in the 'explanation' field.
            Ensure your response is RFC8259 compliant JSON with no additional content.
            Given Prompt : {prompt}
"""


system_message_4 = """
    You're an agent that can evaluate user defined metrics provided in the form of text prompt. Your task is to judge whether the text prompt follows certain standards as defined below.

    Standards:

    1. Language should be clear and easy to understand.
    2. The scope and context should be set correctly.
    3. The intent should be clarified.
    4. The text prompt should be in question format, meant to ask whether a output is performing at the desired level.

    Follow the steps to generate your response.

    1. Evaluate the prompt as per the standards defined above.
    2. Set the `ambiguity` field to `true` if the above criteria are not met, else set it as `false`
    3. Provide a brief explanation for your assessment in the `explanation` field.
    4. Generate a new prompt in a question format that is clear, concise and unambiguous.

    prompt: {prompt}

    Here's an example of sample input-output:

    sample input: verify the output from provided information

    output:

    ```
    {{
        "ambiguous": false,
        "explanation": "Verify is an ambiguous term used without any information"
        "suggested_prompt": "Is my output relevant to the input provided?"
    }}

    Present your final output as a valid JSON dictionary, formatted as shown in the example above. You will return a JSON dictionary which can be loaded using `json.loads()` in python. Avoid `\\n` in your response.
"""
