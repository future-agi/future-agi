rag_generate_subqueries_prompt = """
You are tasked with analyzing a given query, decomposing it into subqueries, and providing relevance scores for each subquery. Follow these instructions carefully:

1. You will be given a query in the following format:
<query>
{query}
</query>

2. Analyze the query and decompose it into subqueries by processing the original query step by step. If multiple subqueries are not possible, return the original query as the only subquery.

3. For each subquery, assign a relevance score based on how much of the original query is answered by the subquery. Use the following scale:
   - Very High: 1.0
   - High: 0.8
   - Average: 0.6
   - Low: 0.4
   - Very Low: 0.2
   - Irrelevant: 0.0

   Note: The sum of all relevance scores for all subqueries must not exceed 1.0.

4. If a subquery is dependent on a previously generated subquery, indicate this by including a "parent" field in the output.

5. For each subquery, provide a brief explanation for the assigned relevance score.

6. Structure your output in JSON format as follows:
   [
     {{
       "subquery": "subquery text",
       "relevance": relevance_score,
       "parent": "[parent subquery] if applicable else []",
       "explanation": "explanation for the subquery relevance score"
     }},
     {{
       // Additional subqueries...
     }}
   ]

7. Examples of good and bad responses:

   Good response:
   [
     {{
       "subquery": "What is the capital of France?",
       "relevance": 0.6,
       "parent": [],
       "explanation": "This subquery addresses the main part of the question but doesn't cover the population aspect."
     }},
     {{
       "subquery": "What is the population of the capital of France?",
       "relevance": 0.4,
       "parent": ["What is the capital of France?"],
       "explanation": "This subquery covers the population aspect and depends on the answer to the first subquery."
     }}
   ]

   Bad response:
   [
     {{
       "subquery": "What is the capital of France?",
       "relevance": 0.5,
       "explanation": "This is half of the question."
     }},
     {{
       "subquery": "What is the population of Paris?",
       "relevance": 0.5,
       "explanation": "This is the other half of the question."
     }}
   ]

8. Important reminders:
   - Provide only the JSON output, nothing else.
   - If no subqueries are possible, return the original query with a relevance of 1.0.
   - Ensure that the sum of all relevance scores does not exceed 1.0.
   - Always include an explanation for each relevance score.

Now, analyze the given query and provide your response in the specified JSON format and max 64000 tokens only should be returned and do not start with ```json and end with ``` just return the json structure.
"""

rag_analyze_subqueries_gain_prompt_old = """
You are tasked with analyzing a query and a list of documents to generate knowledge gained to answer the query. You will iterate through the documents step by step, evaluating the information gained and relevance to the query.

Here are the key concepts you need to understand:

- Query: The question that needs to be answered
- List of Documents: The context provided to answer the query
- Information Gained: A parameter between 0-4 where:
  0 - No information gained
  1 - Less information gained
  2 - Average information gained
  3 - Good information gained
  4 - Sufficient information gained
- Relevancy: How much of the query is answered by using the document. Relevancy is binary, either 0 or 1.

First, review the query:
<query>
{query}
</query>

Next, review any dependent subqueries (if provided):
<dependent_subqueries>
{dependent_subqueries}
</dependent_subqueries>

Now, examine the documents provided:
<documents>
{documents}
</documents>

As you iterate through the documents, follow these steps:
1. Read the document carefully.
2. Determine if the document contains information relevant to the query.
3. Assess if the information is new compared to what you've learned from previous documents.
4. Assign an information gained score (0-4) based on the relevance and novelty of the information.
5. Determine the relevancy (0 or 1) of the document to the query.
6. Provide a brief explanation for the relevancy.
7. If there are dependent subqueries, evaluate their relevance to the document.

After analyzing each document, provide your output in the following JSON-compliant format and max 8000 tokens only should be returned:

[
  {{
    "document_no": 1,
    "information_gained": 0,
    "relevancy": 0,
    "relevancy_explanation": "",
    "relevancy_of_dependent_subqueries": [
      {{
        "dependent_subquery_no": 1,
        "dependent_subquery": "",
        "relevance_of_dependent_subquery": 0,
        "documents_considered": [],
        "relevancy_explanation": ""
      }}
    ]
  }},
  {{
    "document_no": 2,
    "information_gained": 0,
    "relevancy": 0,
    "relevancy_explanation": "",
    "relevancy_of_dependent_subqueries": [
      {{
        "dependent_subquery_no": 1,
        "dependent_subquery": "",
        "relevance_of_dependent_subquery": 0,
        "documents_considered": [],
        "relevancy_explanation": ""
      }}
    ]
  }}
]

For handling dependent subqueries:
1. Evaluate each dependent subquery against all documents considered so far.
2. Include all relevant documents in the "documents_considered" field.
3. Provide a brief explanation of why the documents are relevant to the dependent subquery.

Remember:
- Information is only gained if the document mentions something related to the query and new to existing information gained from past documents.
- Maintain a history of previous documents as you iterate.
- Be objective and consistent in your evaluations.

Provide your analysis in the JSON format specified above. Do not include any additional text or explanations outside of the JSON structure.

POINT TO NOTE: Output should be in JSON format only and max 64000 tokens only should be returned and do not start with ```json and end with ``` just return the json structure.
"""


rag_analyze_subqueries_gain_prompt = """

You are tasked to analyze a query and a list of documents to assess how much each document helps answer the query.

Definitions:
	•	Query: The question to be answered.
	•	Documents: Context to evaluate.
	•	Information Gained: Score from 0–4 (0: None, 4: Sufficient).
	•	Relevancy: 0 or 1, based on whether the document helps answer the query.

Inputs:

<query>{query}</query>
<dependent_subqueries>{dependent_subqueries}</dependent_subqueries>
<documents>{documents}</documents>

Steps for each document:
	1.	Read the document.
	2.	Is it relevant to the query? Assign relevancy: 0 or 1.
	3.	Does it add new info? Assign information gained: 0–4.
	4.	Briefly explain relevancy.
	5.	For each dependent subquery:
	•	Evaluate relevance.
	•	Add related document numbers in documents_considered.
	•	Explain.

Output Format (JSON only, max 64000 tokens):

[
  {{
    "document_no": 1,
    "information_gained": 0,
    "relevancy": 0,
    "relevancy_explanation": "",
    "relevancy_of_dependent_subqueries": [
      {{
        "dependent_subquery_no": 1,
        "dependent_subquery": "",
        "relevance_of_dependent_subquery": 0,
        "documents_considered": [],
        "relevancy_explanation": ""
      }}
    ]
  }}
]

Note:
	•	Be concise.
	•	Track previous documents while scoring.
	•	Return only the JSON structure.
  •	Do not start with ```json and end with ``` just return the json structure.

"""
