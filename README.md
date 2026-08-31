# Chess Arena

A competition server for a workshop. Attendees write chess bots, connect them,
and watch a live ELO leaderboard on a projector.

**Attendees do not read this file.** Point them at
[chess-bot-starter-kit/README.md](chess-bot-starter-kit/README.md).

## Run the server

Once per checkout:

```
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

To let attendees on the same network reach you, bind to all interfaces and give
them your machine's IP address when starting:

```
JOIN_CODE=workshop2026 ADMIN_TOKEN=change-me .venv/bin/python -m chess_server --host 0.0.0.0 --port 8004
```

`JOIN_CODE` is what you write on the whiteboard. `ADMIN_TOKEN` is yours alone.
Neither has a default and the server refuses to start without them.

For local run:

```
JOIN_CODE=workshop2026 ADMIN_TOKEN=change-me .venv/bin/python -m chess_server --port 8004
```

## Put it on the projector

```
http://localhost:8004/dashboard/
```

Four live boards plus the leaderboard. Leave it open — it updates itself.

## Environment

| Variable      | Required | Default    |
| ------------- | -------- | ---------- |
| `JOIN_CODE`   | yes      | none       |
| `ADMIN_TOKEN` | yes      | none       |
| `DB_PATH`     | no       | `arena.db` |

`DB_PATH` is relative to the directory you start the server in. Delete that file
to reset the arena; the server rebuilds it and re-seeds the three reference bots
on the next start.

## Check it is alive

```
curl http://localhost:8004/health
```

Returns JSON. `"db_writable": true` and a small `last_tick_age_ms` mean it is
healthy.

## Tests

```
.venv/bin/pytest -q
```

## If something breaks

- **`ModuleNotFoundError: chess_server`** — re-run `.venv/bin/python -m pip install -e .`.
  The editable install does not notice a new top-level package on its own.
- **`Cannot start the arena server: JOIN_CODE is empty`** — you dropped one of
  the two required variables.
- **Attendees cannot connect** — you are bound to `127.0.0.1`. Restart with
  `--host 0.0.0.0`.
