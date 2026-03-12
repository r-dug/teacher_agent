"""Lesson REST proxy — thin pass-through with session validation."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..http_client import get as get_http
from ..rate_limiter import limiter
from ..session_store import store

router = APIRouter(prefix="/lessons", tags=["lessons"])


def _require_session(session_id: str):
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return entry


async def _proxy_get(path: str, params: dict = None) -> Response:
    """Forward a GET to the backend and return the raw response."""
    http = get_http()
    resp = await http.get(path, params=params)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


async def _proxy_delete(path: str) -> Response:
    http = get_http()
    resp = await http.delete(path)
    return Response(content=resp.content, status_code=resp.status_code)


async def _proxy_patch(path: str, body: bytes, content_type: str) -> Response:
    http = get_http()
    resp = await http.patch(
        path, content=body, headers={"content-type": content_type}
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


# ── routes ─────────────────────────────────────────────────────────────────────

@router.post("/decompose")
async def decompose_pdf(request: Request, x_upload_token: str = Header(...)):
    """Forward multipart PDF upload to backend. Auth via X-Upload-Token."""
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/lessons/decompose",
        content=body,
        headers={
            "content-type": request.headers.get("content-type", ""),
            "x-upload-token": x_upload_token,
        },
    )
    if resp.status_code >= 400:
        import logging
        logging.getLogger(__name__).error(
            "Backend /lessons/decompose returned %s: %s", resp.status_code, resp.text
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@router.get("")
async def list_lessons(x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    user_id = entry.user_id
    if not user_id:
        # Backwards compat: session was created before user_id was stored in the
        # in-memory store. Look it up from the backend and cache it.
        http = get_http()
        resp = await http.get(f"/internal/sessions/{x_session_id}")
        if resp.status_code == 200:
            user_id = resp.json().get("user_id", "")
            entry.user_id = user_id
    return await _proxy_get("/lessons", params={"user_id": user_id})


@router.get("/{lesson_id}")
async def get_lesson(lesson_id: str, x_session_id: str = Header(...)):
    _require_session(x_session_id)
    return await _proxy_get(f"/lessons/{lesson_id}")


@router.patch("/{lesson_id}")
async def update_lesson(lesson_id: str, request: Request, x_session_id: str = Header(...)):
    _require_session(x_session_id)
    body = await request.body()
    return await _proxy_patch(
        f"/lessons/{lesson_id}", body, request.headers.get("content-type", "application/json")
    )


@router.delete("/{lesson_id}", status_code=204)
async def delete_lesson(lesson_id: str, x_session_id: str = Header(...)):
    _require_session(x_session_id)
    return await _proxy_delete(f"/lessons/{lesson_id}")


@router.get("/{lesson_id}/page/{page_number}")
async def get_lesson_page(
    lesson_id: str,
    page_number: int,
    x_session_id: str | None = Header(default=None),
):
    """Proxy PDF page image from backend (used by SlideViewer <img> tag).

    Session validation is best-effort: browsers send no custom headers for
    <img src=...> requests.  The backend already enforces lesson ownership.
    """
    if x_session_id is not None:
        _require_session(x_session_id)
    return await _proxy_get(f"/lessons/{lesson_id}/page/{page_number}")
