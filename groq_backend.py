import os
from urllib.error import URLError

from groq import APIError, Groq


def ask_groq(question: str, reference: str) -> str:
    """Generate an experimental answer using Groq."""
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise URLError("Groq API key not found in the .env file.")

    client = Groq(
        api_key=api_key,
        timeout=30,
        max_retries=0,
    )

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an experimental support assistant. "
                        "Answer in English using only the reference text. "
                        "If the reference does not answer the question, "
                        "say that you do not know. Do not invent facts."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Reference text:\n{reference}\n\n"
                        f"Question: {question}"
                    ),
                },
            ],
            reasoning_effort="low",
            max_completion_tokens=512,
        )

    except APIError as error:
        status = error.status_code
        raise URLError(f"Groq request failed. Status code: {status}") from None

    if not response.choices:
        return ""

    answer = response.choices[0].message.content

    if not answer:
        return ""

    return answer.strip()