import os
from urllib.error import URLError

from google import genai
from google.genai import types
from google.genai.errors import APIError
from httpx import HTTPError


MODEL_NAME = "gemini-3.6-flash"


def ask_gemini(question: str, reference: str) -> str:
    """Generate an experimental answer using Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise URLError("Gemini API key not found in the .env file.")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),
    )

    prompt = (
        "You are an experimental support assistant. "
        "Answer in English using only the reference text. "
        "If the reference does not answer the question, "
        "say that you do not know. Do not invent facts.\n\n"
        f"Reference text:\n{reference}\n\n"
        f"Question: {question}"
    )

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
        )
    except APIError as error:
        status_code = getattr(error, "code", "unknown")
        raise URLError(
            f"Gemini request failed. Status code: {status_code}."
        ) from None
    except HTTPError:
        raise URLError(
            "Gemini connection failed or timed out."
        ) from None

    answer = response.text

    if not answer:
        return ""

    return answer.strip()