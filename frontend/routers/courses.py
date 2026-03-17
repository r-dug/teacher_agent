"""Courses REST proxy — thin pass-through with session validation."""

from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request, Response

from ..http_client import get as get_http
from ..session_store import store
from .lessons import _require_session, _get_user_id

router = APIRouter(prefix="/courses", tags=["courses"])


async def _proxy_get(path: str, params: dict = None) -> Response:
    http = get_http()
    resp = await http.get(path, params=params)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


async def _proxy_post(path: str, body: bytes, content_type: str, params: dict = None) -> Response:
    http = get_http()
    resp = await http.post(
        path, content=body, headers={"content-type": content_type}, params=params
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


async def _proxy_patch(path: str, body: bytes, content_type: str, params: dict = None) -> Response:
    http = get_http()
    resp = await http.patch(
        path, content=body, headers={"content-type": content_type}, params=params
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


async def _proxy_delete(path: str, params: dict = None) -> Response:
    http = get_http()
    resp = await http.delete(path, params=params)
    return Response(content=resp.content, status_code=resp.status_code)


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_courses(x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    return await _proxy_get("/courses", params={"user_id": user_id})


@router.post("")
async def create_course(request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        "/courses",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.get("/{course_id}")
async def get_course(course_id: str, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    return await _proxy_get(f"/courses/{course_id}", params={"user_id": user_id})


@router.patch("/{course_id}")
async def update_course(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_patch(
        f"/courses/{course_id}",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.delete("/{course_id}", status_code=204)
async def delete_course(
    course_id: str,
    x_session_id: str = Header(...),
    cascade_lessons: bool = False,
):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    params: dict[str, str] = {"user_id": user_id}
    if cascade_lessons:
        params["cascade_lessons"] = "true"
    return await _proxy_delete(f"/courses/{course_id}", params=params)


@router.post("/{course_id}/publish")
async def publish_course(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        f"/courses/{course_id}/publish",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.post("/textbook/draft")
async def create_textbook_draft(request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        "/courses/textbook/draft",
        body,
        request.headers.get("content-type", "multipart/form-data"),
        params={"user_id": user_id},
    )


@router.get("/{course_id}/chapters")
async def get_course_chapters(course_id: str, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    return await _proxy_get(f"/courses/{course_id}/chapters", params={"user_id": user_id})


@router.patch("/{course_id}/chapters")
async def update_course_chapters(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_patch(
        f"/courses/{course_id}/chapters",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.post("/{course_id}/advisor/start")
async def advisor_start(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        f"/courses/{course_id}/advisor/start",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.post("/{course_id}/advisor/message")
async def advisor_message(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        f"/courses/{course_id}/advisor/message",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.post("/{course_id}/advisor/finalize")
async def advisor_finalize(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    body = await request.body()
    return await _proxy_post(
        f"/courses/{course_id}/advisor/finalize",
        body,
        request.headers.get("content-type", "application/json"),
        params={"user_id": user_id},
    )


@router.post("/{course_id}/decompose/start")
async def start_decompose(course_id: str, request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    if not entry.is_admin:
        return Response(content='{"detail":"Admin access required"}', status_code=403, media_type="application/json")
    user_id = await _get_user_id(x_session_id, entry)
    raw = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    body = dict(raw or {})
    body["notify_session_id"] = x_session_id
    return await _proxy_post(
        f"/courses/{course_id}/decompose/start",
        json.dumps(body).encode("utf-8"),
        "application/json",
        params={"user_id": user_id},
    )


@router.get("/{course_id}/decompose/status")
async def decompose_status(
    course_id: str,
    x_session_id: str = Header(...),
    job_id: str | None = None,
):
    entry = _require_session(x_session_id)
    user_id = await _get_user_id(x_session_id, entry)
    params: dict[str, str] = {"user_id": user_id}
    if job_id:
        params["job_id"] = job_id
    return await _proxy_get(f"/courses/{course_id}/decompose/status", params=params)
