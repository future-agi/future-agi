import os

# To use Claude instead, set ANTHROPIC_API_KEY.
ACME_KEY = os.environ["ACME_API_KEY"]


def answer(question: str) -> str:
    return f"Support agent received: {question}"


if __name__ == "__main__":
    print(answer("hello"))
