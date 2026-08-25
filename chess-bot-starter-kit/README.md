# Your chess bot

You edit one file: `bot.py`. Nothing else.

## Setup

Once, from this directory:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Every command below uses `.venv/bin/python` — the interpreter you just set up.

## 1. Play some games offline

No server needed. This is how you test changes:

```
.venv/bin/python arena.py --bots bot.py ref_bots/ref_greedy.py --games 4 --seed 7
```

You get a table of wins, losses, draws, average time per move, and how often
each bot flagged or played an illegal move.

Three opponents to try, weakest first: `ref_bots/ref_random.py`,
`ref_bots/ref_greedy.py`, `ref_bots/ref_depth3.py`.

Beat `ref_greedy` consistently and you are ready to go live.

## 2. Go live

Register once. Ask the workshop host for the join code and the server address.

```
.venv/bin/python run.py register --name "Mr MaC" --owner "MaC Group 21" \
    --join-code workshop2026 --server http://localhost:8004
```

That saves your bot's token to `.env` next to `bot.py`. **The token is your
bot's identity** — keep the file, do not share it, do not commit it. It is
never printed.

Then play, as often as you like:

```
.venv/bin/python run.py play
```

It loads the saved token, waits to be paired, and plays until you press Ctrl-C.
Your bot appears on the projector. Edit `bot.py`, stop, and run it again.

**Use your own name as `--owner`. Do not copy your neighbour's.** Two bots with
the same owner are never paired for a rated game, so a whole table registering
as `team1` will sit there producing no rating movement at all and no error to
tell you why.

`--name` and `--owner` accept letters, digits, spaces, `_` and `-`, up to 32
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

- **`ModuleNotFoundError: chess_client` or `chess_core`** — you are in the wrong
  directory, or the setup step didn't run. Run from the folder containing
  `bot.py`, and `.venv/bin/pip install -r requirements.txt` if you haven't.
- **`Could not reach the arena server`** — check `--server` with the workshop
  host, and that they are still running.
- **`No saved bot token`** — you have not registered yet, or `.env` was
  deleted. Run the `register` command again.
- **Registration failed** — usually a wrong `--join-code`, or a `--name`
  someone already took. Pick another name.
- **My bot never gets a game** — it needs an opponent. Wait; the server pairs
  you as soon as one is free.
- **Nothing on the leaderboard moves** — check your `--owner` is unique.

## Rules

- Three illegal moves in one game and you forfeit it.
- Your clock starts when you receive the position, not when you start thinking.
- `board.move_stack` is empty in your bot. You get the position, not the game
  history.
