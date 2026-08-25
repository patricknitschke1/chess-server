# Your chess bot

You edit one file: `bot.py`. Nothing else.

## Setup

Once, from the folder above this one (the repository root):

```
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Then come back here. Every command below is run from this directory, and uses
`../.venv/bin/python` — the interpreter you just set up.

```
cd starter-kit
```

## 1. Play some games offline

No server needed. This is how you test changes:

```
../.venv/bin/python arena.py --bots bot.py ref_bots/ref_greedy.py --games 4 --seed 7
```

You get a table of wins, losses, draws, average time per move, and how often
each bot flagged or played an illegal move.

Three opponents to try, weakest first: `ref_bots/ref_random.py`,
`ref_bots/ref_greedy.py`, `ref_bots/ref_depth2.py`.

Beat `ref_greedy` consistently and you are ready to go live.

## 2. Go live

Create `play.py` next to `bot.py`, paste this, and fill in the four values at
the top:

```python
from chess_client import ChessClient
from bot import choose_move

SERVER = "http://localhost:8000"   # ask the workshop host
NAME = "Sirius"                    # your bot's name, shown on the leaderboard
OWNER = "ada lovelace"             # YOUR OWN NAME - see the warning below
JOIN_CODE = "workshop2026"         # ask the workshop host

client = ChessClient(SERVER)
client.register(NAME, OWNER, JOIN_CODE)
print(f"{NAME} is live. Press Ctrl-C to stop.")
client.run(choose_move)
```

Then:

```
../.venv/bin/python play.py
```

It registers you, waits to be paired, and plays until you stop it. Your bot
appears on the projector.

**Use your own name as `OWNER`. Do not copy your neighbour's.** Two bots with
the same owner are never paired for a rated game, so a whole table registering
as `team1` will sit there producing no rating movement at all and no error to
tell you why.

`NAME` and `OWNER` accept letters, digits, spaces, `_` and `-`, up to 32
characters. No `@`, so an email address will be rejected.

## 3. Make it better

Only `choose_move` in `bot.py` matters:

```python
def choose_move(board: chess.Board, clock: ClockView) -> chess.Move:
```

- `board` is a [python-chess](https://python-chess.readthedocs.io/) `Board`.
  `board.legal_moves` is every move you may play; return one of them.
- `clock.my_ms` is your remaining time in milliseconds, `clock.opponent_ms` is
  theirs. Run out and you lose on time.

The bot you start with searches two moves ahead and counts material. Things
worth trying:

- Raise `SEARCH_DEPTH`. Watch the `Avg(ms)` and `Flags` columns in the arena —
  depth costs time, and losing on time is still losing.
- Score more than material: a knight in the centre is worth more than one in the
  corner.
- Try captures first in `search`. Alpha-beta prunes far more when good moves
  come first.

Change one thing, re-run the arena, keep it if the score went up.

## Common problems

- **`ModuleNotFoundError: chess_client`** — you are in the wrong directory. Run
  from the folder containing `bot.py`.
- **`ModuleNotFoundError: chess_core`** — the setup step did not run. Go up one
  folder and run `.venv/bin/python -m pip install -e .`.
- **`Could not reach the arena server`** — check `SERVER` with the workshop
  host, and that they are still running.
- **Registration failed** — usually a wrong `JOIN_CODE`, or a `NAME` someone
  already took. Pick another name.
- **My bot never gets a game** — it needs an opponent. Wait; the server pairs
  you as soon as one is free.
- **Nothing on the leaderboard moves** — check your `OWNER` is unique.

## Rules

- Three illegal moves in one game and you forfeit it.
- Your clock starts when you receive the position, not when you start thinking.
- `board.move_stack` is empty in your bot. You get the position, not the game
  history.
