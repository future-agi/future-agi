template_plan_prompt = """
Your task is to evaluate the accuracy and effectiveness of an output generated from a specific prompt template. The evaluation will be based on provided information and judging criteria. Your goal is to break down this task into clear, actionable steps and create a comprehensive execution plan.

First, carefully read and analyze the following judging criteria:
Judging Criteria: {question}

To complete this task, follow these steps:

1. Analyze the Judging Criteria to get key factors that will be used for evaluation.


2. Analyze the prompt template to Determine how well it guides the generation of the desired output.

3. Evaluate the output to Determine if the output accurately uses the the instructions in the prompt template.

4. Extract essential elements from provided information (input, prompt template, output, and context if given) and organize them into a concise list for reference during evaluation.

5. Break Down the Judging Criteria into specific, actionable subtasks.

6. Establish a most logical order to address the subtasks.

7. Evaluate Subtasks to Address Each Subtask, describe how to solve it using the relevant information from the provided details.

8. Generate a step by step plan to evaluate the output on judging criteria looking at provided information (input, prompt template, output, and context if given). Your response should be structured as follows:

<plan>
[provide your detailed plan here]
<plan>

Remember, your output should be list of step By step detailed plan to follow to evaluate the output.
"""

template_cot_response_prompt = """
Task: Evaluate  the accuracy and effectiveness of an output generated from a given   prompt template, based on the provided information and the evaluation plan. Follow these steps carefully:

1. Review the judging criteria:
Judging Criteria: {question}

2. Examine the plan for evaluation:
{plan}

6. Evaluate the effectiveness of the prompt template based on how well the output meets the judging criteria and follows the evaluation plan. Consider factors such as accuracy, relevance, completeness, and adherence to the specified format or requirements.

7. Provide a detailed justification for your evaluation. Discuss specific strengths and weaknesses of the prompt template, referencing parts of the input, output, judging criteria, provided information and evaluation plan as necessary.

8. Include few instances where the output could have been more relevant to the input and include them in your explanation.

9. Recognize that the issue may be with the model or the prompt template and make your best guess to guide the user.

10. Make it quick and easy to read, with actionable insights.

11. Present your evaluation in the following format:
<judgment>
[Provide a clear, concise statement of your overall judgment on the relevance of the output according to the evaluation criteria]
</judgment>

Remember to be thorough, objective, and base your evaluation solely on the provided information. Do not make assumptions or introduce external information not present in the given context.
"""

template_plan_prompt_whole = """

        Judging Criteria: {question}

        Task: Develop a comprehensive plan to verify the accuracy of the output  based on the provided information above and matching the Judging Criteria. The plan should be broken down into clear, actionable subtasks.

        Plan:

        1.	Understand the Judging Criteria: Analyze the criteria to determine the factors used to assess the output.
        2.	Extract Key Details: Identify crucial elements from the provided information, including the output, user input, and other mentioned information.
        3.	Break Down the Judging Criteria: Decompose the criteria into specific subtasks for detailed examination.
        4.	Determine Sequence: Establish the order in which the subtasks should be addressed.
        5.	Address Each Subtask: Solve each subtask using the relevant information from the provided information.
        6.	Integrate Results: Combine the outcomes from each subtask to form a cohesive final assessment.
        7.	Validate the Solution: Ensure the solution meets the judging criteria and correctly evaluates the output.

        Elaboration: Please provide detailed steps for each phase, using the key information extracted from the provided information.
        """

template_cot_response_prompt_whole = """

        Judging Criteria: {question}

        Task: Using the plan provided, produce a Judgment that evaluates whether the output aligns with the  given information above, and meets the judging criteria. Include a detailed explanation within the Judgment.

        Plan:
        {plan}

        Execution:
        Follow the steps outlined in the plan to systematically verify the output. Provide a final Judgment with a thorough explanation based on this evaluation.
        """
