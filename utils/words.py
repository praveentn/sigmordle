import math
import random
import hashlib
from pathlib import Path
from collections import Counter

WORDS_FILE = Path(__file__).parent.parent / "data" / "words.txt"

_words: list[str] | None = None
_word_set: set[str] | None = None
_pattern_cache: dict[tuple[str, str], tuple[int, ...]] = {}

# Pattern values: 0=absent ⬛, 1=present wrong-position 🟨, 2=correct 🟩
ABSENT, PRESENT, CORRECT = 0, 1, 2
EMOJI = {ABSENT: "⬛", PRESENT: "🟨", CORRECT: "🟩"}
EMPTY_ROW = "⬜⬜⬜⬜⬜"


def _load() -> list[str]:
    global _words, _word_set
    if _words is None:
        with open(WORDS_FILE) as f:
            _words = [w.strip().upper() for w in f if len(w.strip()) == 5 and w.strip().isalpha()]
        _word_set = set(_words)
    return _words


def get_all_words() -> list[str]:
    return _load()


def is_valid(word: str) -> bool:
    _load()
    return word.upper() in _word_set  # type: ignore[operator]


def get_daily_word(date_str: str) -> str:
    words = _load()
    seed = int(hashlib.sha256(f"sigmordle-{date_str}".encode()).hexdigest(), 16)
    return words[seed % len(words)]


def get_random_word() -> str:
    return random.choice(_load())


def compute_pattern(guess: str, target: str) -> tuple[int, ...]:
    key = (guess, target)
    cached = _pattern_cache.get(key)
    if cached is not None:
        return cached

    pattern = [ABSENT] * 5
    target_pool = list(target)

    # Pass 1 — correct positions
    for i, (g, t) in enumerate(zip(guess, target)):
        if g == t:
            pattern[i] = CORRECT
            target_pool[i] = ""

    # Pass 2 — present but wrong position
    for i, g in enumerate(guess):
        if pattern[i] == ABSENT:
            for j, t in enumerate(target_pool):
                if t == g:
                    pattern[i] = PRESENT
                    target_pool[j] = ""
                    break

    result = tuple(pattern)
    _pattern_cache[key] = result
    return result


def pattern_to_emoji(pattern: tuple[int, ...]) -> str:
    return "".join(EMOJI[p] for p in pattern)


def filter_words(guess: str, pattern: tuple[int, ...], possible: list[str]) -> list[str]:
    return [w for w in possible if compute_pattern(guess, w) == pattern]


def get_remaining(guesses: list[str], patterns: list[tuple[int, ...]], all_words: list[str] | None = None) -> list[str]:
    remaining = list(all_words or _load())
    for g, p in zip(guesses, patterns):
        remaining = filter_words(g, p, remaining)
    return remaining


def compute_expected_entropy(guess: str, possible: list[str]) -> float:
    if not possible:
        return 0.0
    counts: Counter = Counter(compute_pattern(guess, t) for t in possible)
    n = len(possible)
    return sum(-c / n * math.log2(c / n) for c in counts.values())


def information_gained(n_before: int, n_after: int) -> float:
    if n_before <= 1:
        return 0.0
    if n_after == 0:
        return math.log2(n_before)
    return math.log2(n_before / n_after)


def letter_states(guesses: list[str], patterns: list[tuple[int, ...]]) -> dict[str, int]:
    """Return the best known state for each letter guessed so far."""
    states: dict[str, int] = {}
    for guess, pattern in zip(guesses, patterns):
        for ch, state in zip(guess, pattern):
            # Keep the best (highest) state seen for this letter
            if ch not in states or state > states[ch]:
                states[ch] = state
    return states


def build_keyboard_lines(
    guesses: list[str], patterns: list[tuple[int, ...]]
) -> tuple[list, list[str], list[str], list[str]]:
    """Return (correct_slots[5], present_ordered, absent_sorted, untried_sorted).

    correct_slots: 5-element list; each entry is the confirmed letter or None.
    present_ordered: letters in wrong position, in word-discovery order.
    absent_sorted: letters not in the word, alphabetical.
    untried_sorted: letters not yet guessed, alphabetical.
    """
    correct_slots: list = [None] * 5
    present_ordered: list[str] = []
    present_seen: set[str] = set()
    absent_set: set[str] = set()
    tried: set[str] = set()

    for guess, pattern in zip(guesses, patterns):
        for i, (ch, p) in enumerate(zip(guess, pattern)):
            tried.add(ch)
            if p == CORRECT:
                correct_slots[i] = ch
            elif p == PRESENT:
                if ch not in present_seen:
                    present_ordered.append(ch)
                    present_seen.add(ch)
            elif p == ABSENT:
                absent_set.add(ch)

    absent_sorted  = sorted(absent_set)
    untried_sorted = [ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if ch not in tried]
    return correct_slots, present_ordered, absent_sorted, untried_sorted


def compute_points(num_guesses: int, max_guesses: int) -> int:
    if max_guesses == 0:
        return 0
    ratio = num_guesses / max_guesses
    if ratio <= 1 / 6:
        return 10
    elif ratio <= 2 / 6:
        return 7
    elif ratio <= 3 / 6:
        return 5
    elif ratio <= 4 / 6:
        return 3
    elif ratio <= 5 / 6:
        return 2
    else:
        return 1


def streak_bonus(streak: int) -> int:
    """Bonus points for consecutive daily wins (capped at +5)."""
    return min(streak - 1, 5) if streak > 1 else 0
