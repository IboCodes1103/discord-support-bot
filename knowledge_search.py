import re
from dataclasses import dataclass


# Words that show a sentence is probably a question, wherever they appear.
INTERROGATIVES = {
    "what", "whats", "how", "why", "who", "where", "when", "which",
    "wondering", "explain", "tell", "was", "wie", "warum", "wer", "wo",
    "wann", "welche", "welcher",
}

# Helper verbs that start a question ("Does AzubiVerse cost money?").
AUXILIARIES = {
    "is", "are", "do", "does", "can", "could", "will", "would", "should",
    "ist", "sind", "gibt", "kann", "kostet",
}

# Very common words that carry no meaning for the search.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "my",
    "me", "i", "it", "its", "in", "on", "at", "for", "and", "or", "do",
    "does", "did", "you", "your", "there", "this", "that", "with", "can",
    "could", "will", "would", "azubiverse", "der", "die", "das", "ist",
    "ein", "eine", "und", "ich", "mein", "meine",
}

# A long text without punctuation is split in front of these words.
SPLIT_STARTERS = (
    "what", "how", "why", "who", "where", "when", "which", "does", "do",
    "is", "are", "can", "could", "will", "would", "should",
)

# Messages up to this length are always treated as a question.
SHORT_MESSAGE_WORDS = 8

# Sentences longer than this are split into smaller parts.
LONG_SEGMENT_WORDS = 14


@dataclass
class Match:
    """A question found in a message and the knowledge entry for it."""

    question: str
    entry: dict


def tokenize(text: str) -> list[str]:
    """Split a text into lowercase words without punctuation."""
    cleaned = re.sub(r"['’`]", "", text.casefold())
    return re.findall(r"[^\W_]+", cleaned)


def stem(word: str) -> str:
    """Remove a plural ending so that 'discounts' matches 'discount'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"

    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]

    return word


def content_words(text: str) -> frozenset[str]:
    """Return the meaningful words of a text."""
    return frozenset(
        stem(word) for word in tokenize(text) if word not in STOPWORDS
    )


def split_segments(message: str) -> list[str]:
    """Split a message into sentences and, if needed, smaller parts."""
    segments = []

    # Sentences, lines, and comma-separated parts are checked on their own.
    for part in re.split(r"(?<=[.!?])\s+|\n+|\s*[,;]\s+", message):
        part = part.strip()

        if not part:
            continue

        if len(part.split()) <= LONG_SEGMENT_WORDS:
            segments.append(part)
            continue

        pattern = r"\s+(?=(?:" + "|".join(SPLIT_STARTERS) + r")\b)"
        pieces = re.split(pattern, part, flags=re.IGNORECASE)
        segments.extend(piece.strip() for piece in pieces if piece.strip())

    return segments


THANKS_WORDS = {
    "thanks", "thank", "thx", "ty", "cheers", "danke", "dankeschön", "merci",
    "bye", "goodbye", "cya", "tschüss", "ciao",
}

GREETING_WORDS = {
    "hi", "hii", "hello", "hey", "heyy", "hallo", "moin", "servus", "yo",
    "morning", "evening", "sup",
}

# Longer messages probably contain a real request, so they are not smalltalk.
MAX_THANKS_WORDS = 10
MAX_GREETING_WORDS = 5


def detect_smalltalk(message: str) -> str | None:
    """Recognize short greetings and thank-you messages."""
    tokens = tokenize(message)

    if any(token in THANKS_WORDS for token in tokens):
        return "thanks" if len(tokens) <= MAX_THANKS_WORDS else None

    if any(token in GREETING_WORDS for token in tokens):
        return "greeting" if len(tokens) <= MAX_GREETING_WORDS else None

    return None


def looks_like_question(segment: str, message_length: int) -> bool:
    """Check whether a part of a message is probably a question."""
    if "?" in segment or message_length <= SHORT_MESSAGE_WORDS:
        return True

    tokens = tokenize(segment)

    if any(token in INTERROGATIVES for token in tokens):
        return True

    return any(token in AUXILIARIES for token in tokens[:3])


def score_entry(words: frozenset[str], entry: dict) -> int:
    """Count how well the words of a question match an entry."""
    keyword_sets = {content_words(keyword) for keyword in entry["keywords"]}

    return sum(
        len(keyword_set)
        for keyword_set in keyword_sets
        if keyword_set and keyword_set <= words
    )


def search_knowledge(
    message: str,
    knowledge_base: list[dict],
    max_questions: int = 3,
    require_question: bool = True,
) -> list[Match]:
    """Find known questions anywhere in a message."""
    message_length = len(message.split())
    matches = []
    used_entries = set()

    for segment in split_segments(message):
        if require_question and not looks_like_question(segment, message_length):
            continue

        words = content_words(segment)
        best_index = None
        best_score = 0

        for index, entry in enumerate(knowledge_base):
            score = score_entry(words, entry)

            if score > best_score:
                best_index = index
                best_score = score

        # Skip parts without a match and entries that were already found.
        if best_index is None or best_index in used_entries:
            continue

        used_entries.add(best_index)
        matches.append(
            Match(question=segment[:300], entry=knowledge_base[best_index])
        )

        if len(matches) >= max_questions:
            break

    return matches
