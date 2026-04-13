"""Evals + training data proxy — forwards to backend admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from ..http_client import get as get_http
from .admin_guard import require_admin_session

router = APIRouter(prefix="/admin/evals", tags=["evals"])
training_router = APIRouter(prefix="/admin/training", tags=["training"])


@router.get("/runs")
async def list_runs(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/evals/runs", params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/runs/{filename}")
async def get_run(filename: str, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get(f"/admin/evals/runs/{filename}",
                          params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/compare")
async def compare_runs(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    params = dict(request.query_params)
    params["actor_user_id"] = entry.user_id
    resp = await http.get("/admin/evals/compare", params=params)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


# ── Eval runner ──────────────────────────────────────────────────────────────

@router.post("/run")
async def start_run(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/admin/evals/run",
        content=body,
        params={"actor_user_id": entry.user_id},
        headers={"content-type": "application/json"},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/run-status/{run_id}")
async def run_status(run_id: str, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get(f"/admin/evals/run-status/{run_id}",
                          params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/cases")
async def list_cases(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/evals/cases",
                          params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


# ── Training data ────────────────────────────────────────────────────────────

@training_router.get("/examples")
async def list_examples(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    params = dict(request.query_params)
    params["actor_user_id"] = entry.user_id
    resp = await http.get("/admin/training/examples", params=params)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@training_router.get("/annotations")
async def get_annotations(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/training/annotations",
                          params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@training_router.post("/annotate")
async def annotate(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/admin/training/annotate",
        content=body,
        params={"actor_user_id": entry.user_id},
        headers={"content-type": "application/json"},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@training_router.post("/convert-to-eval")
async def convert_to_eval(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/admin/training/convert-to-eval",
        content=body,
        params={"actor_user_id": entry.user_id},
        headers={"content-type": "application/json"},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")
