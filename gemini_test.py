from dotenv import load_dotenv
from gemini_backend import ask_gemini


# Load the API key from the local .env file.
load_dotenv()

question = "Does AzubiVerse offer a free certificate?"
reference = (
    "AzubiVerse is free to use and has no advertisements."
)

print("Sending the test question to Gemini...")

try:
    answer = ask_gemini(question, reference)
    print("\nGemini answer:")
    print(answer or "Gemini returned no text.")

except Exception as error:
    print("\nThe Gemini request did not finish successfully.")
    print(f"Error type: {type(error).__name__}")
    print(f"Details: {error}")