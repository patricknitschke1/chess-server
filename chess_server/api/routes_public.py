"""The unauthenticated read surface (role spec §8.1, §8.4; design §10.4, §14).

Every route here reads on the reader connection and executor, outside
`write_lock`: a dashboard refresh must never queue behind the game loop.
"""
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from chess_server.api.state import AppState, get_state

router = APIRouter()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    app_state: AppState = get_state(request)
    hub = app_state.hub
    return StreamingResponse(
        hub.stream(hub.subscribe()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
