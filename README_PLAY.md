# How to Play Sigmordle

Sigmordle is a Discord Wordle-style word guessing game with entropy analytics, streaks, and server leaderboards — all inside private Discord threads.

---

## How a Game Works

1. Run `/wordle play` — the bot opens a **private thread** just for you and posts your game board.
2. **Type your 5-letter guess directly in the thread** as a normal message. No slash command needed.
3. The bot deletes your message, updates the board embed with coloured tiles, and logs your entropy stats.
4. Keep guessing until you solve it or run out of attempts.
5. A **Give Up** button is always visible on the board if you want to forfeit and see the answer.

`/wordle guess <word>` is a slash-command fallback that works the same way from outside the thread.

---

## Tile Colours

| Tile | Meaning |
|---|---|
| 🟩 | Correct letter, **correct position** |
| 🟨 | Correct letter, **wrong position** |
| ⬛ | Letter is **not in the word** |

The board image also shows a QWERTY keyboard with letters coloured by their best-known state.

---

## Game Modes

| Mode | Command | Description |
|---|---|---|
| Daily | `/wordle play` | Same word for the whole server, every day. Contributes to streaks and server stats. |
| Free Play | `/wordle play mode:freeplay` | Random word. Play as many times as you like. Does not affect your daily streak. |

### Difficulty

Add `max_guesses:N` (1–10) to make a game harder or easier:

```
/wordle play max_guesses:4          Daily word, only 4 guesses
/wordle play mode:freeplay max_guesses:10   Freeplay with 10 guesses
```

The default is 6 guesses.

---

## Slash Commands

### Playing

| Command | Description |
|---|---|
| `/wordle play` | Start today's daily word |
| `/wordle play mode:freeplay` | Start a free play game |
| `/wordle play max_guesses:N` | Set guess limit (1–10) |
| `/wordle guess <word>` | Submit a guess (slash-command fallback) |
| `/wordle board` | Jump to your active game thread |
| `/wordle giveup` | Forfeit and reveal the answer |

### Stats & Leaderboards

| Command | Description |
|---|---|
| `/wordle stats` | Your personal stats |
| `/wordle stats user:@someone` | Another player's stats |
| `/wordle leaderboard` | Server top players by total points |
| `/wordle leaderboard limit:15` | Show up to 25 players |
| `/wordle daily` | Today's results for the whole server |
| `/wordle server` | Server-wide aggregate stats |
| `/wordle history` | Your last 5 games |
| `/wordle history limit:10` | Your last 10 games (up to 15) |
| `/wordle help` | In-game help and command list |

### Reminders (Server Admin Only)

| Command | Description |
|---|---|
| `/remind channel #channel` | Set the reminder channel and enable reminders |
| `/remind timezone America/New_York` | Set the local midnight timezone (IANA name) |
| `/remind status` | Show current reminder configuration |
| `/remind test` | Fire a reminder immediately to test |
| `/remind off` | Disable reminders |

---

## Scoring

Points are awarded when you **solve** the word. Giving up or running out of guesses earns 0 points.

| Guesses Used | Base Points |
|---|---|
| 1 | **10 pts** |
| 2 | **7 pts** |
| 3 | **5 pts** |
| 4 | **3 pts** |
| 5 | **2 pts** |
| 6+ | **1 pt** |
| Failed / Given up | **0 pts** |

### Streak Bonus

Solve the **daily word** on consecutive days to earn bonus points on top of your base score:

| Consecutive Daily Wins | Bonus |
|---|---|
| 1 day | +0 |
| 2 days | +1 |
| 3 days | +2 |
| 4 days | +3 |
| 5 days | +4 |
| 6+ days | +5 (max) |

Your streak resets if you miss a day or fail to solve the daily word.

### Server Streak

A separate server-wide streak tracks how many consecutive days **at least one player** on the server solved the daily word.

---

## Your Stats (`/wordle stats`)

| Stat | What it shows |
|---|---|
| Games played / won | All-time counts and win percentage |
| Total points | Cumulative score across all games |
| Current streak | Consecutive daily wins right now |
| Max streak | Your all-time best consecutive streak |
| Average solve time | Mean time from game start to final guess |
| Guess distribution | Bar chart showing how many games you solved in 1, 2, 3 … guesses |
| Favourite openers | Your most-used first words |

---

## Entropy — What Is It?

Every guess is scored for *information content* in **bits**.

- **Expected entropy** — how much information this guess *should* give on average, based on how it splits all remaining candidate words.
- **Actual bits gained** — how much the pattern you actually received told you: `log₂(words_before ÷ words_after)`.

A guess that narrows 5917 words down to 147 gains `log₂(5917/147) ≈ 5.33 bits`.

**Example entropy log:**
```
G1  CRANE   5917 → 147 words  |  5.33 bits  (expected 5.18, Δ+0.15)  🟢
G2  SLOTH    147 →   8 words  |  4.20 bits  (expected 3.87, Δ+0.33)  🟢
G3  SUITE     8  →   1 word   |  3.00 bits  (expected 2.51, Δ+0.49)  🟢
```

**Quality indicators:**
- 🟢 Actual > expected — lucky or clever guess
- 🟡 Close to expected — par
- 🔴 Actual < expected — received less info than average

Use `/wordle daily` to compare your entropy log with other players on the same word.

---

## Daily Reminders

If a server admin has configured reminders (`/remind channel`), you'll receive a daily message at midnight (in the server's timezone) that includes:

- All players tagged so nobody misses the daily
- Current streaks for active players
- A snapshot of the server leaderboard
- A daily word-history fact
- Quick-action buttons: **Play Daily · Free Play · Leaderboard · My Stats · Daily Results**

---

## Tips

1. **High-entropy openers** like `CRANE`, `SLATE`, `RAISE`, or `ADIEU` eliminate the most candidates on your first guess.
2. After a 🟩 hit, play words that test *other* unknown letters — don't waste guesses reconfirming what you already know.
3. Use `/wordle daily` after you finish to compare your entropy efficiency with other players on the same word.
4. Your **guess distribution** in `/wordle stats` shows whether you tend to solve early or late — useful for spotting whether you're playing too safe or too aggressive.
5. Streak bonus caps at +5, so a streak of 10 earns the same bonus as a streak of 6. Points still compound every day you stay consistent.
