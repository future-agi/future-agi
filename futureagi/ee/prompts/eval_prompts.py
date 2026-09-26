eval_prompt = """You will be given some instructions by a person, on how to evaluate a response with respect to a query and the chat history leading up to the query. You will take the initial instructions and convert it into convert it to 5 very specific non overlapping instructions which cover all the possible nuances keeping in mind the original instructions. You will then return the expanded instructions. You will return the response in a RFC8259 compliant JSON response with the serial number as keys, starting from 1. There should be nothing in the response apart from the JSON.\n\nInstructions: {eval_instructions}"""

AUDIO_AGENT_LLM_SYSTEM_INSTRUCTION = (
    "When evaluating tasks, provide extremely concise reasoning (1-2 liners max). "
    "Only mention the single most critical factor that influenced your assessment. "
    "Avoid all explanations of scoring methodology."
)


summary_judgement_prompt = """
Analyze the evaluation judgments and provide 2-3 bullet points that highlight major issues and actionable improvements. Focus on common problems identified by the majority, excluding outliers.

{chat_conversation}

Strictly provide the concise summary in one actionable bullet points only.
Start with "Evaluation: "
"""


summary_judgement_prompt_ragrank = """

You are tasked with generating a comprehensive summary based on a list of documents relevant to a specific subquery. Each document in the list contains a relevancy score, an explanation of its relevance, and information used for ranking. Your goal is to analyze these documents, rank them, and create a concise summary that captures the key points and insights related to the subquery.

{query_doc_info}

Please follow these steps to complete the task:

1. Carefully read through the relevancy explanations for all documents in the list.

2. Analyze the document-level information, including the relevancy scores and any additional ranking information provided.

3. Based on your analysis, rank the documents in order of their importance to the subquery. Consider factors such as relevancy score, information gained, and the depth of insights provided.

4. Identify the document that provides the most relevant information and note the rationale behind its top ranking.

5. Look for any notable gaps in information or conflicting insights across the documents.

6. Create a concise summary that addresses the following points:
   a. Key information related to the subquery
   b. The ranking of documents and the rationale behind the ranking
   c. Highlights from the most relevant document
   d. Any significant information gaps or conflicting insights
   e. Overall context and implications of the information gathered

7. Ensure your summary is focused and provides a clear understanding of the topic in the context of the subquery.

Please provide your response in the following format:

<summary>
[Your concise summary here, addressing all the points mentioned above]
</summary>

"""

summary_judgement_prompt_ragrank_v2 = """
You are a ranking critique which will be given a main query , the subqueries related to main query that was used for answering the main query, a list of documents as context which were used for answering the subqueries, and relevance of each document in the context with respect to subqueries.

Analyze this information and come up with a summary explaining the ranking of all documents with respect to main query . Use only subqueries and document relevancy information for your analysis without using predefined knowledge.

Current used ranking used is also mentioned along with documents given in context.

Final scores used for judging ranking is calculated by multiplying "information gain" and "final_relevance". Consider final_scores for analysing ranking.

Query:
{query}

Context:
{context}

Subqueries and their relevance info:
{subquery_relevance_info}

NOTE: RETURN ONLY THE RANKING OF CONTEXT DOCUMENTS, NO OTHER TEXT IN VERY CONCISE FORMAT.

"""


qualitative_eval_parameter_prompt = """You are evaluating a person who answers queries of a customer using some context. You will be given the exact criteria based on which you need to evaluate the person. You will expand the criteria into 5 non-overlapping sub-criterias, which can be used to evaluate the person. The sub-criterias will have the context, the question and the answer of the person. You will return the response in a RFC8259 compliant JSON response with the keys as the serial numbers starting from 1 and the values as the sub-criterias. You will return nothing but the JSON response.
            Criteria: {criteria}"""


qualitative_eval_parameter_prompt_v2 = """
You are an AI evaluator tasked with refining evaluation criteria into exactly three measurable subcriteria with weighted importance. Provided criteria will be under the <criteria> tag. Follow these steps:
	1.	Criteria: Expand the criteria into exactly three specific, non-overlapping subcriteria.
	2.	Question Format: Frame each subcriterion as a question explicitly assessing an evaluation metric.
	3.	Weights: Assign a weight (decimal between 0 and 1) to each subcriterion. Weights need not sum to 1.
	4.	Output: Present the subcriteria and weights in JSON format (RFC8259-compliant), using serial numbers as keys.
<criteria> {criteria} </criteria>
	{{
  "1": ["Is the information provided in the response accurate?", 0.6],
  "2": ["Does the response cover all key points mentioned in the query?", 0.2],
  "3": ["Is the language used in the response clear and concise?", 0.4],
}}
Task: Break the provided criteria into exactly three subcriteria with weights and return only the JSON object.
"""

eval_subcriteria_prompt = """You are evaluating a chatbot who answers queries of a customer using some context against a criteria. You will give a judgment in 2-3 sentences about how well the chat fulfills the criteria. Please read the criteria and the chat history carefully. You will return the response in a RFC8259 compliant JSON response with the key as "judgment" and the value as the judgment. You will return nothing but the JSON response.
Criteria: {subcriteria}
Chat History: {chat_history}
"""

score_prompt = """
You are tasked with summarizing an analysis of a query-response pair. Your goal is to distill the analysis into a single word summary that captures the overall quality of the response.

Here is the analysis you need to summarize:
<analysis>
{judgment}
</analysis>

To summarize the analysis, follow these steps:
1. Carefully read and understand the provided analysis.
2. Determine the overall sentiment and quality assessment from the analysis.
3. Strictly Choose one of the following summary words that best matches the analysis:
   - 'good'
   - 'very good'
   - 'bad'
   - 'very bad'

Your output must be in RFC8259 compliant JSON format. The JSON should contain a single key 'summary' with the chosen summary word('good','very good','bad','very bad') as its value.

Here are some examples of valid outputs:
{{"summary": "good"}}

Important notes:
- Your response should contain nothing but the JSON object.
- Do not include any explanations or additional text outside the JSON.
- Ensure that your chosen summary accurately reflects the sentiment of the analysis.

Now, summarize the given analysis and provide your response in the specified JSON format.
"""


generate_correct_answer_prompt = """You are given a chat history, which ends with the user asking something. Then the chatbot answers the query. But the answer could have been better when evaluated against a criteria. You will be given the chat history, the original response, the criteria and the evaluation of the original response. You will think step by step about the following points to improve the response.
- Understand the user's query and the chat history.
- Understand the chatbot's response and the evaluation of the response.
- Think about how to improve the chatbot's response to better address the criteria.
- Think about the original response was not satisfying the criteria and improve upon it.
- Always keep the answers concise and to the point.
- Answering the user's query correctly is of the utmost importance.

You will generate a better response taking into account all of the provided information. You will return nothing but the response.
Chat History: {chat_history}
Criteria: {criteria}
Evaluation: {evaluation}
Original Response: {original_response}
"""


generate_fixes_for_answer_prompt = """
You are an AI assistant assigned to analyze and improve chatbot responses. Your objective is to provide constructive feedback on how to enhance a given answer based on a concise summary of its evaluation across different criteria. Follow these steps carefully:

1. Review the following chat history:
<chat_history>
{chat_history}
</chat_history>

2. Examine the answer provided by the chatbot:
<answer>
{answer}
</answer>

3. Consider the evaluation of this answer across different criteria:
<evaluation>
{evaluation}
</evaluation>

4.	Analyze the response and its evaluation thoroughly to improve the answer.
5.	Generate a list of constructive feedback points on how to improve the answer. enhancements beyond the evaluation.
6.	Present your improvements as a concise, organized list of points.

Remember, even if the evaluation is highly positive, aim to provide suggestions that could further elevate the quality of the response. Your goal is to offer valuable, insightful recommendations that contribute to more effective and polished chatbot interactions.

"""


apply_fixes_prompt = """
You are an expert in refining chatbot answers. Your task is to enhance an existing answer by applying a list of specified fixes, considering the chat history and evaluation provided. Follow these steps closely:

1.	Review the chat history to understand the full context:
<chat_history>
{chat_history}
</chat_history>

2.	Examine the original answer provided by the chatbot:
<original_answer>
{answer}
</original_answer>


3.	Take into account the evaluation of the original answer, which includes feedback on various criteria:
<evaluation>
{evaluation}
</evaluation>

4.	Consider the list of fixes that need to be applied to improve the answer while Strictly follow same format for improved answer if it is specified in chat history for an output:
<fixes>
{fixes}
</fixes>


5.	Output only the improved answer as your response, without any additional commentary or explanations .

Your goal is to produce a refined answer that seamlessly addresses all concerns raised in the evaluation and incorporates all suggested fixes. The final answer should serve as a direct replacement for the original one within the given chat history.

"""
qualitative_eval_parameter_prompt_v3 = """You are evaluating a chatbot who answers queries of a customer using some context. You will be given a list of  criterias based on which you need to evaluate the chatbot. You will expand the criterias into distinct, non-overlapping subcriterias, which can be used to evaluate the chatbot. You will also provided a relative weight for each subcriteria. Keep in mind some subcriterias will only be applicable if the corresponding context is available. Like for e.g. if the subcriterias are related to the chatbot asking clarifying queston, that will only matter if there is something confusing. The subcriterias will be of a single line. You will return the response in a RFC8259 compliant JSON response with the keys as the serial numbers starting from 1 and the values as the sub-criterias, weight tuple. Weight will be between 0 and 1. You will return nothing but the JSON response. The JSON response should be without any special characters which will cause issues while decoding to json.
Criterias:
{criterias}"""


combine_subcriterias_prompt = """
You will be given a series of subcriterias in the format of a list. These subcriterias will be used to evaluate a chat history by a chatbot. The subcriterias can be possibly related to each other or two or more subcriterias can be evaluating the same thing.

Your task is to understand the original list of subcriterias and what it is trying to achieve. Then you will come up with a new list of subcriterias which tests the same things as the list provided to you, but this new list of subcriterias will consolidate all overlapping subcritrias. It will also remove any subcriteria that is redundant and reword any subcriteria which is not worded properly. All the points in the new list should be concise and non-overlapping. You will return a weight associated with it, on how important it is. You will be given the user defined metrics based on which the original list of subcriterias was generated. You will use this user defined metrics to decide how important the new subcriterias are in form of relative weights. The weights should sum up to 1. You will return a JSON response with the keys as the serial numbers starting from 1 and the values as the new subcriterias, weight tuple. You will return nothing but the JSON response. The JSON response should be without any special characters which will cause issues while decoding to json.
I will tip you 100$ if you return a valid JSON.

User defined metrics:
{user_defined_metrics}

List of subcriterias:
{criterias}
"""


FEEDBACK_FEWSHOT_PROMPT = """EXAMPLES:
    Below are examples of output samples retrieved using vector similarity search. These samples reflect situations where users provided feedback or corrections earlier. Use these examples to guide the evaluation of the main input and output based on the specified evaluation criteria.

    GUIDELINES:
    1. **Strict Feedback Alignment**:
    - Always prioritize and adhere strictly to the user's feedback, as it represents their preferences and requirements even in cases of toxic or biased language.
    - Avoid making assumptions about the user's use case or context. Your role is to implement their feedback exactly as provided.

    2. **Context and Learning**:
    - Use provided feedback examples to understand user preferences.
    - Learn and adapt from user feedback to refine future outputs.

    IMPORTANT:
    - Always prioritize user feedback, even if it contradicts general practices or common standards.
    - Your responses must align entirely with the user's corrections, as you lack knowledge of their specific use case or broader context.
    - Your evaluation explanation should seem natural and not directly reference these instructions.
    """
