"""Voices and STT languages proxy — no auth required (public metadata)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..http_client import get as get_http

router = APIRouter(tags=["voices"])


@router.get("/voices")
async def list_voices():
    http = get_http()
    resp = await http.get("/voices")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/stt-languages")
async def list_stt_languages():
    http = get_http()
    resp = await http.get("/stt-languages")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/stt-providers")
async def list_stt_providers():
    http = get_http()
    resp = await http.get("/stt-providers")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/voice-arches")
async def list_voice_arches():
    http = get_http()
    resp = await http.get("/voice-arches")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/stt-models")
async def list_stt_models(request: Request):
    http = get_http()
    resp = await http.get("/stt-models", params=dict(request.query_params))
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")
