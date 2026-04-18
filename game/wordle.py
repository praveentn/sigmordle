from __future__ import annotations
import json
from dataclasses import dataclass, field
from utils.words import compute_pattern, is_valid, compute_points


@dataclass
class EntropyEntry:
    guess: str
    n_before: int
    n_after: int
    expected_bits: float
    actual_bits: float

    def to_dict(self) -> dict:
        return {
            "guess": self.guess,
            "n_before": self.n_before,
            "n_after": self.n_after,
            "expected_bits": round(self.expected_bits, 4),
            "actual_bits": round(self.actual_bits, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EntropyEntry":
        return cls(
            guess=d["guess"],
            n_before=d["n_before"],
            n_after=d["n_after"],
            expected_bits=d.get("expected_bits", 0.0),
            actual_bits=d.get("actual_bits", 0.0),
        )


class WordleGame:
    def __init__(
        self,
        game_id: int,
        target: str,
        guesses: list[str],
        patterns: list[tuple[int, ...]],
        status: str,
        max_guesses: int,
        mode: str,
        entropy_log: list[EntropyEntry],
    ):
        self.game_id     = game_id
        self.target      = target
        self.guesses     = guesses
        self.patterns    = patterns
        self.status      = status
        self.max_guesses = max_guesses
        self.mode        = mode
        self.entropy_log = entropy_log

    # ── Class method constructors ─────────────────────────────────────────────

    @classmethod
    def from_db(cls, row: dict) -> "WordleGame":
        guesses  = json.loads(row["guesses"])
        patterns = [tuple(p) for p in json.loads(row["patterns"])]
        elog_raw = json.loads(row.get("entropy_log") or "[]")
        elog     = [EntropyEntry.from_dict(e) for e in elog_raw]
        return cls(
            game_id     = row["game_id"],
            target      = row["target"],
            guesses     = guesses,
            patterns    = patterns,
            status      = row["status"],
            max_guesses = row["max_guesses"],
            mode        = row["mode"],
            entropy_log = elog,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def num_guesses(self) -> int:
        return len(self.guesses)

    @property
    def remaining_guesses(self) -> int:
        return self.max_guesses - self.num_guesses

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_won(self) -> bool:
        return self.status == "won"

    @property
    def is_lost(self) -> bool:
        return self.status == "lost"

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, word: str) -> str | None:
        """Return an error message, or None if the guess is valid."""
        if len(word) != 5:
            return f"`{word}` must be exactly 5 letters."
        if not word.isalpha():
            return f"`{word}` must contain only letters."
        if not is_valid(word):
            return f"`{word}` is not in the word list."
        if word in self.guesses:
            return f"You already guessed `{word}`."
        return None

    # ── Apply a guess ─────────────────────────────────────────────────────────

    def apply_guess(self, word: str, entropy_entry: EntropyEntry) -> tuple[int, ...]:
        """Record the guess and return the pattern. Does NOT update status."""
        pattern = compute_pattern(word, self.target)
        self.guesses.append(word)
        self.patterns.append(pattern)
        self.entropy_log.append(entropy_entry)

        if all(p == 2 for p in pattern):
            self.status = "won"
        elif self.num_guesses >= self.max_guesses:
            self.status = "lost"

        return pattern

    # ── Points ────────────────────────────────────────────────────────────────

    def compute_points(self, streak: int = 0) -> int:
        if not self.is_won:
            return 0
        base    = compute_points(self.num_guesses, self.max_guesses)
        from utils.words import streak_bonus
        bonus   = streak_bonus(streak)
        return base + bonus

    # ── Serialisation helpers ─────────────────────────────────────────────────

    def guesses_json(self) -> str:
        return json.dumps(self.guesses)

    def patterns_json(self) -> str:
        return json.dumps([list(p) for p in self.patterns])

    def entropy_log_json(self) -> str:
        return json.dumps([e.to_dict() for e in self.entropy_log])

    # ── Total entropy accrued so far ──────────────────────────────────────────

    @property
    def total_bits(self) -> float:
        return sum(e.actual_bits for e in self.entropy_log)

    @property
    def total_expected_bits(self) -> float:
        return sum(e.expected_bits for e in self.entropy_log)
