tag_for_reason_prompt = """You are the very best tag generator. You will take a review. This review is a critique of outputs of some evaluation criteria. You will generate tags, which reflect the nature of the review covering all the important information, covering uniqueness of the judgement. You will pick up key points which the review states and make a tag out of those. Covering all aspects of the judgement. You can make anywhere between two to ten tags, but you will keep it to a minimum to keep the tags unique. Prepend it with the sentiment of the tag. By sentiment, I mean if the tag represents a good thing for the critique or a bad thing.  It can only be "POSITIVE" or "NEGATIVE" followed by : and the tag extracted from the judgement. For e.g. POSITIVE:tag1 for positive tag1 and NEGATIVE:tag2 for negative tag2. You will return a list of tags with the key "tags" in JSON format. Do not insert backticks.
                    Evaluation Criteria: {eval_instructions}
                    Critique: {reason}"""

# tag_for_reason_prompt = """You are the very best tag generator. You will take a review. This review is a critique of outputs of an AI model against some evaluation criteria. You will generate tags, which reflect the nature of the review. You will pick up key points which the review states and make a tag out of those. You can make anywhere between 2 to 5 tags, but you will keep it to a minimum to keep the tags unique. The least number of tags is desirable, covering all aspects. Each tag can have a maximum of 2 words. Prepend it with the sentiment of the tag. By sentiment, I mean if the tag represents a good thing for the critique or a bad thing. It can only be "POSITIVE" or "NEGATIVE". For e.g. POSTIVE:concise, NEGATIVE:inaccurate, NEGATIVE:bad-chronology. You will return a list of tags with the key "tags" in JSON format. Do not insert backticks.
#                     Evaluation Criteria: {eval_instructions}
#                     Critique: {reason}"""


tag_for_reason_prompt_v2 = """
You are tasked with analyzing a critique of an AI's performance based on a specific criteria. You will generate tags, which reflect the nature of the review covering all the important information, covering uniqueness of the judgement. You will pick up key points which the review states and make a tag out of those. Covering all aspects of the judgement.

You will be provided with two pieces of information:
<criteria>
{criteria}
</criteria>

<critique>
{critique}
</critique>

Carefully read and analyze the critique. Identify key points, strengths, weaknesses, and notable aspects of the AI's performance mentioned in the critique.

Based on your analysis, generate a list of tags. Each tag should represent a property or characteristic of the AI's performance as described in the critique such that all the tags create a summarized representation critique. For each tag, determine whether it represents a positive or negative aspect of the performance.

Create a list of dictionaries, where each dictionary contains the following keys:
1. "property": A detailed phrase describing the characteristic or aspect of performance.
2. "tag": A short label for the property covering the sentimental information.
3. "sentiment": Either "POSITIVE" or "NEGATIVE", indicating whether this aspect is favorable or unfavorable

Format your response as a JSON array of these dictionaries.

Here's an example of how your output should be structured and dont include ``` in output just a simple list:

```
[
  {{
    "property": "property1",
    "tag": "tag1",
    "sentiment": "POSITIVE"
  }},
  {{
    "property": "property2",
    "tag": "tag2",
    "sentiment": "NEGATIVE"
    }},
  {{
    "property": "property3",
    "tag": "tag2",
    "sentiment": "POSITIVE"
  }}
]
```

Analyze the critique thoroughly and generate appropriate tags that capture the essence of the AI's performance. Ensure that your tags are diverse and cover different aspects mentioned in the critique. Aim to create at one to five tags, but you may create more if the critique contains many distinct points.

Present your final output as a valid JSON array of dictionaries, formatted as shown in the example above. You will return a JSON array which can be loaded using `json.loads()` in python. Avoid `\\n` in your response.
"""
