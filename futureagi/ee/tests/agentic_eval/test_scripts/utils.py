import json
from os import getenv

from openai import OpenAI

# load_dotenv()


# gets API Key from environment variable OPENAI_API_KEY
def get_qualitative_eval_parameter_prompt(eval_instructions):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=getenv("OPENROUTER_API_KEY"),
    )

    completion = client.chat.completions.create(
        model="anthropic/claude-3-opus",
        messages=[
            {
                "role": "user",
                "content": f"""You will be given some instructions by a person, on how to evaluate responses of another person. You will take the 10 very specific instructions which cover all the possible nuances keeping in mind the original instructions. You will then return the expanded instructions. You will return the response in a RFC8259 compliant JSON response with the serial number as keys, starting from 1. There should be nothing in the response apart from the JSON. \n\nInstructions: " + {eval_instructions}""",
            },
        ],
        response_format={"type": "json_object"},
    )

    return completion.choices[0].message.content


def get_score_of_chat_response_against_detailed_criteria(eval_instructions, response):
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=getenv("OPENROUTER_API_KEY"),
    )

    # "\n".join(
    #     [f"{i}: {instruction}" for i, instruction in eval_instructions.items()]
    # )

    prompt = f"""
    You are given a set of 10 instructions. You have to evaluate the response of Person B based on what Person A asked, based on these instructions. The response should be evaluated on the grades of "Very good", "good", "moderate", "bad" and "very bad". You will return the response in a RFC8259 compliant JSON response with the serial number of the instructions as keys, starting from 1 and the corresponding grades as values. There should be nothing in the response apart from the JSON. \n\nInstructions: {eval_instructions}\n\nPerson A: {response["Person A"]}\n\nPerson B: {response["Person B"]}
    """
    completion = client.chat.completions.create(
        model="anthropic/claude-3-opus",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        response_format={"type": "json_object"},
    )
    try:
        return completion.choices[0].message.content
    except:
        return ""
    # return completion.choices[0].message.content


def calculate_score_from_json(json_response):
    score = 0
    for _key, value in json_response.items():
        if value.lower() == "very good":
            score += 2
        elif value.lower() == "good":
            score += 1
        elif value.lower() == "moderate":
            score += 0
        elif value.lower() == "bad":
            score -= 1
        elif value.lower() == "very bad":
            score -= 2
    return score


if __name__ == "__main__":
    expanded_criteria = get_qualitative_eval_parameter_prompt(
        "The responses should be funny with a touch of sarcasm."
    )
    expanded_criteria = json.loads(expanded_criteria)
    print(expanded_criteria)
    llm_response = {
        "Person A": "What is the capital of France?",
        "Person B": "Paris",
    }
    json_response = get_score_of_chat_response_against_detailed_criteria(
        expanded_criteria, llm_response
    )
    json_response = json.loads(json_response)
    print(json_response)
    print(calculate_score_from_json(json_response))
