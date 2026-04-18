# How to Play Sigmordle

Sigmordle is a Discord Wordle-style word guessing game with entropy analytics, streaks, and leaderboards.

## The Basics

Guess the hidden **5-letter word** in as few attempts as possible.

After each guess you get colour-coded feedback:

| Emoji | Meaning |
|---|---|
| 🟩 | Correct letter, **correct position** |
| 🟨 | Correct letter, **wrong position** |
| ⬛ | Letter is **not in the word** |

## Game Modes

| Mode | Description |
|---|---|
| `daily` (default) | Same word for everyone, every day. Counts for streaks and server stats. |
| `freeplay` | Random word, play as many times as you like. |

## Slash Commands

### Starting & Playing

```
/wordle play                         Start today's daily word (6 guesses)
/wordle play max_guesses:4           Daily word with only 4 guesses (harder!)
/wordle play mode:freeplay           Random word, unlimited plays per day
/wordle guess <word>                 Submit your 5-letter guess
/wordle board                        Show your current game board
/wordle giveup                       Reveal the word and forfeit
```

### Stats & Leaderboard

```
/wordle stats                        Your personal stats
/wordle stats user:@someone          Another player's stats
/wordle leaderboard                  Server leaderboard by total points
/wordle daily                        Today's results for the whole server
/wordle server                       Server-wide aggregate stats
/wordle history                      Your last 5 games
/wordle history limit:10             Your last 10 games
/wordle help                         Show in-game help
```

## Scoring

| Guesses Used | Base Points |
|---|---|
| 1 | **10 pts** |
| 2 | **7 pts** |
| 3 | **5 pts** |
| 4 | **3 pts** |
| 5 | **2 pts** |
| 6+ | **1 pt** |
| Failed | **0 pts** |

### Streak Bonus

Win the **daily word** on consecutive days to earn bonus points:

| Streak | Bonus |
|---|---|
| 1 day | +0 |
| 2 days | +1 |
| 3 days | +2 |
| 4 days | +3 |
| 5 days | +4 |
| 6+ days | +5 (max) |

Your streak resets if you miss a day or fail to solve.

## Entropy — What Is It?

Every time you make a guess, the bot calculates:

- **Expected entropy** — how much information this guess *should* give you on average (in bits), based on how it splits all possible remaining words.
- **Actual information gained** — how much the pattern you received actually told you: `log₂(words_before / words_after)` bits.

**Example:**
```
G1  CRANE  →  5917 → 147 words  |  5.33 bits  (expected 5.18, Δ+0.15)
G2  SLOTH  →  147  →   8 words  |  4.20 bits  (expected 3.87, Δ+0.33)
G3  SUITE  →    8  →   1 word   |  3.00 bits  (expected 2.51, Δ+0.49)
```

Higher actual bits = a luckier/better guess. If actual < expected, the pattern you received was less informative than average.

## Server & Daily Stats

- `/wordle daily` shows everyone who played today, ranked by guesses, with a comparison of average entropy across players.
- `/wordle server` shows the most-played words, common opening words, server solve rate, and current server streak.
- **Server streak** — how many consecutive days at least one player on the server solved the daily word.

## Tips

1. **High-entropy openers** like `CRANE`, `SLATE`, `RAISE`, or `ADIEU` eliminate the most candidates on the first guess.
2. After a 🟩 hit, play words that test the *other* unknown letters — don't waste guesses reconfirming what you know.
3. Use `/wordle daily` after you finish to see how your entropy compares with other players on the same word.
4. `/wordle stats` shows your **guess distribution** bar chart — spot whether you tend to solve early or late.
