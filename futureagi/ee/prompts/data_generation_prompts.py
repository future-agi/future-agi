system_message = "You are an expert in analyzing chat history. You will be given a question-answer-action_taken pair and a condition. You need to determine if the condition is satisfied in the given question-answer-action_taken pair."
decompose_prompt = """Decompose the following complex query into simpler queries that can be evaluated:\n\n"
            "Complex query: {complex_query}"""

evaluate_condition_prompt = """Is the following query condition satisfied in the given question-answer-action_taken pair?\n\n"
                    "Condition: {condition}\n\n"
                    "Question: {question}\n\n"
                    "Answer: {answer}\n\n"
                    "Action taken: {action}"""

system_message_human = "You are a human being asking questions. You will get responses from a coding expert. Work on it till the topic is resolved. Write the words 'topic resolved' to end the conversation. As soon as your question is answered, you can end the conversation."
system_message_expert = "You are a coding expert providing answers. You will get questions from a human. Work on it till the question is resolved. Write the words 'topic resolved' to end the conversation."
reaction_prompt = "Based on the following response from the expert, determine the most appropriate user reaction:\n\n{expert_response}\n\nValid reactions: Like, Dislike, Copies the code output, None\n\nReaction:"
user_reaction_content_prompt = "You are an AI assistant that determines the most appropriate user reaction based on the expert's response."
