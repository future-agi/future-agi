RECOMMENDATION_PROMPT = """
        You are a data quality and evaluation expert. Your task is to recommend the most relevant evaluation metrics for a given dataset column based on its name and sample data.

You will be provided with the following information:

Column name:
<column_name>
{column_name}
</column_name>

Sample data for all the column:
<row_data>
{row_data}
</row_data>

Available evals and their categories where that can be used to evaluate the data:
<available_evals>
{evals}
</available_evals>

Available evals description:
<available_evals_description>
{evals_description}
</available_evals_description>

Sample use cases where the evals can be used:
<use_cases>
{use_cases}
</use_cases>


Your task is to analyze the column name and sample data, and then select only the most relevant and valuable evals from the available evals list using their categories and description:

1. Focus on the category that is matching the data.
2. Try to match the data with the evals and their categories and try to give the best match for category that is matching the data and with the evals description.
3. Try to bring diversity in the evals that are being recommended so that it can cover all the categories of the data and evals.

Limit your recommendations to a maximum of {top_n} evaluations. Focus only on high-value evaluations for this column.

Think about the data type, potential patterns, and what qualities would be most important to evaluate for this specific column.

Your final output must be in valid JSON format only with strict following structure:

<output>
{{
  "recommended_evals": ["eval1", "eval2", "eval3"]
}}
</output>

Important: Your response should strictly consist of only the JSON output within the <output> tags. Do not include any additional commentary, explanations, or text outside of the JSON structure.

Response:

        """
