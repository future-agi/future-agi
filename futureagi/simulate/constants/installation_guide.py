INSTALLATION_GUIDE = """
pip install agent-simulate
"""

CHAT_SDK_CODE = """
# export FI_API_KEY="<YOUR_FI_API_KEY>"
# export FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"

import asyncio
import os
from fi.simulate import TestRunner, AgentInput

# Your agent implementation (works with any framework)
async def customer_support_agent(input: AgentInput) -> str:
    \"\"\"
    Custom agent that processes customer inquiries.
    Replace this with your actual agent implementation.
    \"\"\"
    # Get the user's message
    user_message = input.new_message["content"] if input.new_message else ""

    # Example: Call your LLM service (OpenAI, Anthropic, etc.)
    # from openai import AsyncOpenAI
    # client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    # response = await client.chat.completions.create(
    #     model="gpt-4o-mini",
    #     messages=[{{"role": "user", "content": user_message}}]
    # )
    # return response.choices[0].message.content

    # For demo purposes, return a simple response
    return f"Thank you for your message: '{{user_message}}'. I'm here to help!"

async def main():
    # Initialize Test Runner (credentials from env vars)
    runner = TestRunner()

    # Run simulation
    report = await runner.run_test(
        run_test_name= "{run_test_name}",
        agent_callback=customer_support_agent
    )

    print(f"Simulation finished! Processed {{len(report.results)}} test cases")

if __name__ == "__main__":
    asyncio.run(main())

"""
