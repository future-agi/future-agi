EXPLANATION_PROMPT = """
You are tasked with providing a explanation for a given metadata key. This explanation should help understand what possible values the key might have. Follow these instructions carefully:

1. You will be given two pieces of information:
   <metric_key>{metric_key}</metric_key>
   This is the metadata key you need to explain.

   <metric>{metric}</metric>
   This is the sentence from which the key was extracted. Use this for context, but do not reference it directly in your explanation.

2. Create a explanation of what the key represents. This explanation should:
   - Be general and not user-specific
   - Not mention the user, the user input, or any chat history
   - Describe what kind of values this key might have

3. Format your response as a JSON object with two fields:
   - "key": The exact metric_key provided
   - "explanation": Your explanation

4. Additional guidelines:
   - Keep the explanation concise and to the point
   - Focus on what the key represents, not on the specific value in the given sentence
   - Avoid using phrases like "This key represents..." or "This field indicates..."
   - Start your explanation with a capital letter and don't end it with a period

6. Your entire response should be just the JSON object. Do not include any additional text, explanations, or formatting outside of the JSON.

Provide your response now, following all the guidelines above.
"""

EXTRACTING_VALUE_FROM_CHAT_PROMPT = """
You are an metadata extractor tasked with analyzing chat conversations and extracting specific information. Your goal is to identify and extract all relevant information about a given key from the provided chat text.

Here is the chat text you need to analyze:
<chat_text>
{chat_text}
</chat_text>

The key you need to focus on is '{key}', which represents {explanation}. Your task is to extract all information related to this key from the chat text. Keep in mind that the information might not be directly stated and may require analysis and inference.

To extract the information:
1. Carefully read through the entire chat text.
2. Look for any explicit mentions of key: {key} or something related to the explanation: {explanation}.
3. There might not be any explicit mention about {key}. Analyze the context and content of the conversation to infer any implicit information related to {key} or by guessing the possible information for the context and the content.
4. Consider multiple possible values or interpretations if applicable.
5. If no relevant information is found, return an empty array.

Provide your output in JSON format with the key '{key}' and an array of values. The format should be as follows:
{{"key1": ["value1", "value2", ...]}}

If no relevant information is found, return:
{{"key1": []}}

Examples of good outputs:
{{"key1": ["value1", "value2"]}}
{{"key1": []}}

Examples of bad outputs:
{{"key1": "value1"}}  // Not an array
{{"key1": ["value1", "value2"], "other_key": ["value3"]}}  // Includes unrelated key
{{"wrong_key": ["value1", "value2"]}}  // Incorrect key name

Your entire response should be just the JSON object. Do not include any additional text, explanations, or formatting outside of the JSON.

"""

PARSE_FILTER_PROMPT = """
You are tasked with identifying filter conditions in a given metric and returning them in a specific JSON format. Your goal is to extract any filter conditions present and structure them correctly.

Here is the metric you need to analyze:
<metric>
{metric_input}
</metric>

To identify filter conditions:
1. Look for any comparisons or restrictions in the metric.
2. Identify the attribute being filtered (this will be the "key").
3. Determine the value(s) associated with the filter.
4. Infer the appropriate operator based on the context.
5. Determine the data type of the value(s).

If you find any filter conditions, structure your output as a JSON object with a "filter" key containing an array of filter conditions. Each filter condition should have the following fields:
- "key": The attribute being filtered
- "values": An array of filter values (even if there's only one value)
- "data_type": The data type of the values (e.g., "string", "numeric", "boolean", "date")
- "operator": The comparison operator

Valid operators include: =, !=, >, <, >=, <=, in, not in

If no filter conditions are found, return an empty JSON object: {{}}

Here's an example:
Input: "What are the chats where the user was facing an error in their code?"
Output:
{{
    "filter": [{{"key": "status_of_code", "values": ["error"], "data_type": "string", "operator": "="}}]
}}

Remember:
- The "key" represents the high-level description or attribute being filtered.
- The "values" are the specific filter values.
- For example, in "what are the places where Python and C++ is used", the key would be "language" and the values would be ["Python", "C++"].

Analyze the given metric and return only the JSON object representing the filter conditions, if any. Do not include any additional text or explanation in your response.
"""

PARSE_DISTRIBUTION_PROMPT = """
You are tasked with identifying if there's a distribution key in a given metric. A distribution key is the topic on which the user wants to break down the data. Your goal is to analyze the metric and return a JSON object with the appropriate information.

Here are some examples to guide you:

1. If the metric contains a distribution key:
   Input: "Give a distribution across programming languages."
   Output: {{"distribution_key": ["programming-language"]}}

2. If the metric implies a distribution key:
   Input: "How often do users ask for health-related advice?"
   Output: {{"distribution_key": ["advice_topic"]}}

3. If the metric mentions filtering or grouping:
   Input: "Create a filter on human language used"
   Output: {{"distribution_key": ["language"]}}

4. If there's no distribution key:
   Input: "What is the total number of users?"
   Output: {{}}

To identify a distribution key, look for words or phrases that suggest breaking down, grouping, or filtering data. Common indicators include "distribution across," "breakdown by," "grouped by," or mentions of specific categories like languages, topics, or user attributes.

Your output should be a JSON object. If a distribution key is found, include a "distribution_key" key with an array of identified keys. If no distribution key is found, return an empty JSON object.

Here's the metric to analyze:
<metric>
{metric_input}
</metric>

Analyze the metric and provide your output as a JSON object. Return only the JSON object, without any additional explanation or text.
"""


EVENT_GENERATOR_PROMPT = """
As an AI assistant, you've been given a collection of similar conversations to examine. Your objective is to identify and extract common text features from these dialogues.

GUIDELINES:

1. You're encouraged to discover attributes that could be useful in understanding and analyzing text-based conversations.
2. Only include features that are actually present in the provided conversations.
3. Ensure that the features you identify are broad enough to be relevant to various types of conversations.
4. Some examples of generic features are location, age, gender, sentiment and emotion.

For each feature you discover, assign it a descriptive name, provide a general explanation of its significance and the datatype of the feature.
Present your findings in JSON format, with name key as feature name, explanation key as its description and datatype key as its value as datatype of the feature eg: ["string", "numeric", "boolean", "date"].

Output should be a JSON array of objects, each containing the keys "name", "explanation", and "datatype". Don't include any other keys or any other text.

Example output:
{{
   [
      {{"name": "feature1", "explanation": "feature1 explanation", "datatype": "string"}},
      {{"name": "feature2", "explanation": "feature2 explanation", "datatype": "numeric"}},
      {{"name": "feature3", "explanation": "feature3 explanation", "datatype": "boolean"}},
      {{"name": "feature4", "explanation": "feature4 explanation", "datatype": "date"}}
   ]
}}

Conversations:
{conversations}
"""
