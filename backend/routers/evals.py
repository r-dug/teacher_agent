"""Admin eval + training data endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..authz import require_admin
from ..db import connection as db

router = APIRouter(prefix="/admin", tags=["evals"])

Conn = Annotated[asyncpg.Connection, Depends(db.get)]

EVALS_DIR = Path("storage/evals")
CASES_DIR = Path("backend/evals/cases")
DISTILLATION_DIR = Path("storage/distillation")

# Pattern: run_<model>_<YYYYMMDD>_<HHMMSS>.jsonl
RUN_FILE_RE = re.compile(r"^run_(.+)_(\d{8}_\d{6})\.jsonl$")


def _parse_run_file(path: Path) -> list[dict]:
    results = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def _summarize_run(filename: str, results: list[dict]) -> dict:
    """Build summary stats for one run file."""
    m = RUN_FILE_RE.match(filename)
    model = results[0].get("model", "unknown") if results else "unknown"
    ts = None
    if m:
        try:
            ts = datetime.strptime(m.group(2), "%Y%m%d_%H%M%S").replace(
                tzinfo=timezone.utc
            ).isoformat()
        except ValueError:
            pass

    grader_summary: dict[str, dict] = {}
    latencies = []
    for r in results:
        for name, g in r.get("grades", {}).items():
            s = grader_summary.setdefault(name, {"passed": 0, "total": 0})
            s["total"] += 1
            if g.get("passed"):
                s["passed"] += 1
        lat = r.get("latency_ms", 0)
        if lat:
            latencies.append(lat)

    total_cases = len(results)
    total_passed = sum(
        1 for r in results
        if all(g.get("passed") for g in r.get("grades", {}).values())
    )

    return {
        "filename": filename,
        "model": model,
        "timestamp": ts,
        "total_cases": total_cases,
        "total_passed": total_passed,
        "grader_summary": grader_summary,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
    }


@router.get("/evals/runs")
async def list_runs(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)

    if not EVALS_DIR.exists():
        return []

    runs = []
    for path in sorted(EVALS_DIR.glob("run_*.jsonl"), reverse=True):
        try:
            results = _parse_run_file(path)
            runs.append(_summarize_run(path.name, results))
        except Exception:
            continue
    return runs


@router.get("/evals/runs/{filename}")
async def get_run(filename: str, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)

    # Sanitize filename
    if not RUN_FILE_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename format")

    path = EVALS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    results = _parse_run_file(path)
    summary = _summarize_run(filename, results)
    return {"summary": summary, "results": results}


@router.get("/evals/compare")
async def compare_runs(
    a: str = Query(..., description="Filename of run A"),
    b: str = Query(..., description="Filename of run B"),
    actor_user_id: str = "",
    conn: Conn = None,  # type: ignore[assignment]
):
    await require_admin(conn, actor_user_id)

    for fname in (a, b):
        if not RUN_FILE_RE.match(fname):
            raise HTTPException(status_code=400, detail=f"Invalid filename: {fname}")

    path_a = EVALS_DIR / a
    path_b = EVALS_DIR / b
    if not path_a.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {a}")
    if not path_b.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {b}")

    results_a = _parse_run_file(path_a)
    results_b = _parse_run_file(path_b)
    summary_a = _summarize_run(a, results_a)
    summary_b = _summarize_run(b, results_b)

    # Find changed cases
    cases_a = {r["case_id"]: r for r in results_a}
    cases_b = {r["case_id"]: r for r in results_b}
    all_graders = set()
    for r in results_a + results_b:
        all_graders.update(r.get("grades", {}).keys())

    changes = []
    for cid in sorted(set(cases_a) & set(cases_b)):
        for grader in sorted(all_graders):
            ga = cases_a[cid].get("grades", {}).get(grader)
            gb = cases_b[cid].get("grades", {}).get(grader)
            if ga and gb and ga.get("passed") != gb.get("passed"):
                changes.append({
                    "case_id": cid,
                    "grader": grader,
                    "direction": "fixed" if gb["passed"] else "regressed",
                    "detail_a": ga.get("detail", ""),
                    "detail_b": gb.get("detail", ""),
                })

    return {
        "a": summary_a,
        "b": summary_b,
        "changes": changes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Eval Runner (async from dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

_active_runs: dict[str, dict] = {}


class RunEvalRequest(BaseModel):
    model: str
    base_url: str | None = None
    cases_file: str = "backend/evals/cases/seed.jsonl"
    judge_model: str | None = None
    tag: str | None = None


async def _do_eval_run(run_id: str, req: RunEvalRequest) -> None:
    """Execute eval run in a thread, updating _active_runs with progress."""
    import uuid
    from openai import OpenAI

    from ..scripts.run_evals import grade_case, load_cases, run_case, save_results

    def _run():
        api_key = os.environ.get("OPENAI_API_KEY", "ollama")
        client_kw: dict = {"api_key": api_key}
        if req.base_url:
            client_kw["base_url"] = req.base_url
        client = OpenAI(**client_kw)

        judge_client = None
        if req.judge_model:
            judge_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", api_key))

        cases = load_cases(req.cases_file)
        if req.tag:
            cases = [c for c in cases if req.tag in c.get("tags", [])]

        if not cases:
            _active_runs[run_id]["status"] = "failed"
            _active_runs[run_id]["error"] = "No cases to run"
            return

        results = []
        for i, case in enumerate(cases, 1):
            _active_runs[run_id]["progress"] = f"{i}/{len(cases)}"
            try:
                raw = run_case(client, req.model, case)
                grades = grade_case(
                    case, raw["text"], raw["tool_calls"],
                    judge_client=judge_client, judge_model=req.judge_model,
                )
                grades_dict = {
                    name: {"passed": g.passed, "score": g.score, "detail": g.detail}
                    for name, g in grades.items()
                }
                results.append({
                    "case_id": case.get("id", f"case-{i}"),
                    "model": req.model,
                    "response": raw["text"],
                    "tool_calls": raw["tool_calls"],
                    "latency_ms": raw["latency_ms"],
                    "grades": grades_dict,
                })
            except Exception as e:
                results.append({
                    "case_id": case.get("id", f"case-{i}"),
                    "model": req.model,
                    "response": "",
                    "tool_calls": [],
                    "latency_ms": 0,
                    "grades": {"error": {"passed": False, "score": 0.0, "detail": str(e)}},
                })

        path = save_results(req.model, results)
        _active_runs[run_id]["status"] = "completed"
        _active_runs[run_id]["result_filename"] = path.name

    try:
        await asyncio.to_thread(_run)
    except Exception as e:
        _active_runs[run_id]["status"] = "failed"
        _active_runs[run_id]["error"] = str(e)


@router.post("/evals/run")
async def start_eval_run(body: RunEvalRequest, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)

    import uuid
    run_id = str(uuid.uuid4())[:8]
    _active_runs[run_id] = {
        "status": "running",
        "progress": "0/0",
        "model": body.model,
        "started_at": time.time(),
        "result_filename": None,
        "error": None,
    }
    asyncio.create_task(_do_eval_run(run_id, body))
    return {"run_id": run_id}


@router.get("/evals/run-status/{run_id}")
async def get_run_status(run_id: str, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    if run_id not in _active_runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return _active_runs[run_id]


@router.get("/evals/cases")
async def list_case_files(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    if not CASES_DIR.exists():
        return []
    result = []
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        count = sum(1 for line in open(path) if line.strip())
        result.append({"filename": path.name, "case_count": count})
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Training Data Browser
# ═══════════════════════════════════════════════════════════════════════════════

def _example_id(example: dict) -> str:
    """Stable hash-based ID for a training example."""
    raw = json.dumps(example, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _messages_preview(messages: list[dict]) -> str:
    """Extract a short preview from a message list."""
    parts = []
    for msg in messages[-3:]:  # last 3 messages
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(text_parts)
        if content:
            parts.append(f"{role}: {content[:100]}")
    return " | ".join(parts)[:300]


def _load_annotations() -> dict[str, dict]:
    """Load annotations, last-write-wins per example_id."""
    path = DISTILLATION_DIR / "annotations.jsonl"
    if not path.exists():
        return {}
    annotations = {}
    for line in open(path):
        line = line.strip()
        if line:
            try:
                ann = json.loads(line)
                annotations[ann["example_id"]] = ann
            except (json.JSONDecodeError, KeyError):
                continue
    return annotations


@router.get("/training/examples")
async def list_training_examples(
    actor_user_id: str,
    conn: Conn,
    file: str = "training_openai.jsonl",
    offset: int = 0,
    limit: int = 20,
    search: str = "",
):
    await require_admin(conn, actor_user_id)

    # Sanitize filename
    if "/" in file or "\\" in file or ".." in file:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = DISTILLATION_DIR / file
    if not path.exists():
        return {"total": 0, "offset": offset, "limit": limit, "examples": []}

    annotations = _load_annotations()
    examples = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            ex = json.loads(line)
        except json.JSONDecodeError:
            continue
        if search:
            raw = json.dumps(ex, ensure_ascii=False).lower()
            if search.lower() not in raw:
                continue
        eid = _example_id(ex)
        messages = ex.get("messages", [])
        examples.append({
            "example_id": eid,
            "messages_preview": _messages_preview(messages),
            "message_count": len(messages),
            "has_tools": bool(ex.get("tools")),
            "annotation": annotations.get(eid),
        })

    total = len(examples)
    page = examples[offset: offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "examples": page}


@router.get("/training/annotations")
async def get_annotations(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    return _load_annotations()


class AnnotateRequest(BaseModel):
    example_id: str
    rating: str  # "good", "bad", "skip"
    notes: str = ""


@router.post("/training/annotate")
async def annotate_example(body: AnnotateRequest, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    DISTILLATION_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "example_id": body.example_id,
        "rating": body.rating,
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(DISTILLATION_DIR / "annotations.jsonl", "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


class ConvertToEvalRequest(BaseModel):
    example_id: str
    tags: list[str] = []
    expected_tool: str | None = None
    cases_file: str = "seed.jsonl"


@router.post("/training/convert-to-eval")
async def convert_to_eval(body: ConvertToEvalRequest, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)

    # Find the training example by ID
    target = None
    for fname in DISTILLATION_DIR.glob("*.jsonl"):
        if fname.name == "annotations.jsonl":
            continue
        for line in open(fname):
            line = line.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _example_id(ex) == body.example_id:
                target = ex
                break
        if target:
            break

    if not target:
        raise HTTPException(status_code=404, detail="Training example not found")

    # Build eval case from training example
    messages = target.get("messages", [])
    system_prompt = ""
    conversation = []
    for msg in messages:
        if msg.get("role") == "system":
            system_prompt = msg.get("content", "")
        else:
            conversation.append(msg)

    case_id = f"converted-{body.example_id}"
    eval_case = {
        "id": case_id,
        "tags": body.tags or ["converted"],
        "system_prompt": system_prompt,
        "messages": conversation[:-1] if conversation else [],  # all but last (target)
    }
    if body.expected_tool:
        eval_case["expected_behaviors"] = {"should_use_tool": body.expected_tool}

    # Append to case file
    cases_path = CASES_DIR / body.cases_file
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    with open(cases_path, "a") as f:
        f.write(json.dumps(eval_case, ensure_ascii=False) + "\n")

    return eval_case
