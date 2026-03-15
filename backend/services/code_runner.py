"""
Sandboxed code execution service.

Execution is layered:
  1. AST check (Python only) — blocks dangerous imports/builtins before any
     process starts.
  2. Wall-clock timeout — asyncio cancels the subprocess if it runs too long.
  3. bubblewrap (bwrap) — namespace sandbox with no network, read-only OS
     mounts, and a tmpfs scratch directory. Falls back to unsandboxed if bwrap
     is not installed (logs a warning).

Compiled languages (C, C++, Rust, TypeScript) are compiled OUTSIDE bubblewrap
(needs filesystem write access), then only the build artefact is exposed inside
the sandbox via the /code bind-mount.

For python-ml, uv resolves/caches packages outside bubblewrap with a longer
timeout; the cached store is then mounted read-only inside.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import shutil
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path

log = logging.getLogger(__name__)

# ── Runtime registry ──────────────────────────────────────────────────────────

_UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")

_ML_PACKAGES = ["numpy", "pandas", "scikit-learn", "matplotlib", "torch"]
_ML_WITH = [arg for pkg in _ML_PACKAGES for arg in ("--with", pkg)]

RUNTIMES: dict[str, dict] = {
    "python": {
        "run": ["python3"],
        "ext": ".py",
        "compiled": False,
        "timeout_run": 10,
    },
    "python-ml": {
        "run": [_UV, "run", *_ML_WITH],
        "ext": ".py",
        "compiled": False,
        "timeout_warmup": 60,  # package download on first use
        "timeout_run": 30,
        "needs_uv": True,
    },
    "javascript": {
        "run": ["node"],
        "ext": ".js",
        "compiled": False,
        "timeout_run": 10,
    },
    "typescript": {
        "compile": ["tsc", "--target", "ES2020", "--module", "commonjs",
                    "--outDir", "{outdir}", "{src}"],
        "run": ["node"],
        "ext": ".ts",
        "out_ext": ".js",
        "compiled": True,
        "timeout_compile": 30,
        "timeout_run": 10,
    },
    "c": {
        "compile": ["gcc", "-O2", "-o", "{bin}", "{src}"],
        "ext": ".c",
        "compiled": True,
        "timeout_compile": 30,
        "timeout_run": 10,
    },
    "cpp": {
        "compile": ["g++", "-O2", "-std=c++17", "-o", "{bin}", "{src}"],
        "ext": ".cpp",
        "compiled": True,
        "timeout_compile": 30,
        "timeout_run": 10,
    },
    "rust": {
        "compile": ["rustc", "-o", "{bin}", "{src}"],
        "ext": ".rs",
        "compiled": True,
        "timeout_compile": 60,
        "timeout_run": 10,
    },
}

# ── Python AST security check ─────────────────────────────────────────────────

_BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "urllib", "http", "ftplib",
    "smtplib", "telnetlib", "requests", "httpx", "aiohttp",
    "multiprocessing", "ctypes", "cffi", "mmap", "pty", "tty",
    "signal", "resource", "gc", "weakref",
})
_BLOCKED_BUILTINS = frozenset({
    "__import__", "eval", "exec", "open", "compile", "breakpoint", "input",
})


class SecurityError(ValueError):
    pass


def _ast_check(code: str) -> None:
    """Raise SecurityError if the Python code contains dangerous constructs."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # let the interpreter report it naturally

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLOCKED_MODULES:
                    raise SecurityError(f"Import of '{top}' is not allowed in the sandbox.")
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in _BLOCKED_MODULES:
                raise SecurityError(f"Import of '{top}' is not allowed in the sandbox.")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in _BLOCKED_BUILTINS:
                raise SecurityError(f"Use of '{name}()' is not allowed in the sandbox.")


# ── bubblewrap command builder ────────────────────────────────────────────────

_BWRAP = shutil.which("bwrap")


def _bwrap_cmd(inner_cmd: list[str], code_dir: str, runtime: str) -> list[str]:
    """Return a bubblewrap command that runs inner_cmd inside the sandbox."""
    uv_cache = Path.home() / ".cache" / "uv"
    uv_bin = Path(_UV)

    cmd = [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/lib32", "/lib32",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind-try", "/sbin", "/sbin",
        "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/home",
        "--ro-bind", code_dir, "/code",
        "--chdir", "/code",
        "--unshare-all",
        "--new-session",
        "--die-with-parent",
    ]

    # python-ml needs uv and its package cache
    if runtime == "python-ml":
        if uv_cache.exists():
            cmd += ["--ro-bind", str(uv_cache), str(uv_cache)]
        if uv_bin.exists():
            cmd += ["--ro-bind", str(uv_bin), str(uv_bin)]

    cmd += inner_cmd
    return cmd


# ── helpers ───────────────────────────────────────────────────────────────────

async def _run_proc(
    cmd: list[str],
    timeout: float,
    *,
    cwd: str | None = None,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess, wait for completion, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise


async def _stream_proc(
    cmd: list[str],
    timeout: float,
    chunks: asyncio.Queue,
) -> int:
    """
    Stream stdout and stderr of a subprocess into *chunks* as
    {"type": "stdout"/"stderr", "data": str} dicts.
    Returns the exit code.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _pump(stream: asyncio.StreamReader, kind: str) -> None:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            await chunks.put({"type": kind, "data": chunk.decode(errors="replace")})
        await chunks.put(None)  # sentinel

    pump_tasks = [
        asyncio.create_task(_pump(proc.stdout, "stdout")),
        asyncio.create_task(_pump(proc.stderr, "stderr")),
    ]

    sentinels = 0
    deadline = asyncio.get_event_loop().time() + timeout
    try:
        while sentinels < 2:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            item = await asyncio.wait_for(chunks.get(), timeout=remaining)
            if item is None:
                sentinels += 1
            else:
                await chunks.put(item)  # re-queue for the caller to drain
                break  # caller drains from here; we just needed to start
    except asyncio.TimeoutError:
        proc.kill()
        for t in pump_tasks:
            t.cancel()
        raise

    # Let the caller drain; wait for process to exit
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    return proc.returncode or 0


# ── main public API ───────────────────────────────────────────────────────────

async def stream_execution(
    code: str,
    runtime: str,
) -> AsyncGenerator[dict, None]:
    """
    Execute *code* in the configured runtime and yield event dicts:

      {"type": "stdout",  "data": "..."}
      {"type": "stderr",  "data": "..."}
      {"type": "done",    "exit_code": int, "elapsed_ms": int}
      {"type": "error",   "message": "..."}   ← pre-execution failure
    """
    cfg = RUNTIMES.get(runtime)
    if cfg is None:
        yield {"type": "error", "message": f"Unknown runtime: '{runtime}'"}
        return

    # Python-family: AST security check before touching disk
    if runtime.startswith("python"):
        try:
            _ast_check(code)
        except SecurityError as exc:
            yield {"type": "stderr", "data": f"SecurityError: {exc}\n"}
            yield {"type": "done", "exit_code": 1, "elapsed_ms": 0}
            return

    tmpdir = tempfile.mkdtemp(prefix="coderun_")
    try:
        ext = cfg["ext"]
        src_path = os.path.join(tmpdir, f"solution{ext}")
        with open(src_path, "w") as f:
            f.write(code)

        # ── Compile step ──────────────────────────────────────────────────────
        run_inner_cmd: list[str]  # command to run inside the sandbox

        if cfg.get("compiled"):
            compile_template: list[str] = cfg["compile"]

            if runtime == "typescript":
                outdir = os.path.join(tmpdir, "out")
                os.makedirs(outdir, exist_ok=True)
                compile_cmd = [
                    c.replace("{outdir}", outdir).replace("{src}", src_path)
                    for c in compile_template
                ]
                run_inner_cmd = [*cfg["run"], "/code/out/solution.js"]
            else:
                bin_path = os.path.join(tmpdir, "solution")
                compile_cmd = [
                    c.replace("{bin}", bin_path).replace("{src}", src_path)
                    for c in compile_template
                ]
                os.chmod(bin_path, 0o755) if os.path.exists(bin_path) else None
                run_inner_cmd = ["/code/solution"]

            yield {"type": "stderr", "data": f"[compiling {runtime}…]\n"}
            try:
                rc, out, err = await _run_proc(
                    compile_cmd, cfg.get("timeout_compile", 30)
                )
            except asyncio.TimeoutError:
                yield {"type": "stderr", "data": "Compilation timed out.\n"}
                yield {"type": "done", "exit_code": 124, "elapsed_ms": 0}
                return

            if out:
                yield {"type": "stdout", "data": out.decode(errors="replace")}
            if err:
                yield {"type": "stderr", "data": err.decode(errors="replace")}
            if rc != 0:
                yield {"type": "done", "exit_code": rc, "elapsed_ms": 0}
                return

            # Make the binary executable (may not exist until after compile)
            bin_path_obj = Path(tmpdir) / "solution"
            if bin_path_obj.exists():
                bin_path_obj.chmod(0o755)

        else:
            # Interpreted: just reference the source file
            filename = f"solution{ext}"
            if runtime == "python-ml":
                run_inner_cmd = [*cfg["run"], f"python3", f"/code/{filename}"]
                # Correct: uv run ... python3 /code/file
                run_inner_cmd = [_UV, "run", *_ML_WITH, "python3", f"/code/{filename}"]
            elif runtime == "python":
                run_inner_cmd = ["python3", f"/code/{filename}"]
            elif runtime == "javascript":
                run_inner_cmd = ["node", f"/code/{filename}"]
            else:
                run_inner_cmd = [*cfg["run"], f"/code/{filename}"]

        # ── python-ml: warm up uv cache outside bwrap ─────────────────────────
        if cfg.get("needs_uv"):
            warmup_cmd = [_UV, "run", *_ML_WITH, "python3", "-c", "pass"]
            try:
                warmup = await asyncio.create_subprocess_exec(
                    *warmup_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(
                    warmup.communicate(), timeout=cfg.get("timeout_warmup", 60)
                )
            except (asyncio.TimeoutError, FileNotFoundError, OSError):
                pass  # best-effort; proceed anyway

        # ── Build the sandboxed command ───────────────────────────────────────
        if _BWRAP:
            full_cmd = _bwrap_cmd(run_inner_cmd, tmpdir, runtime)
        else:
            log.warning(
                "bwrap not found — running code unsandboxed. "
                "Install bubblewrap: sudo apt install bubblewrap"
            )
            # Adjust paths: /code/X → tmpdir/X
            full_cmd = [
                c.replace("/code/", tmpdir + "/") for c in run_inner_cmd
            ]

        # ── Stream execution ──────────────────────────────────────────────────
        t_start = time.monotonic()
        timeout_run = cfg.get("timeout_run", 10)

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            yield {"type": "error", "message": f"Failed to start process: {exc}"}
            return

        async def _pump(stream: asyncio.StreamReader, kind: str, q: asyncio.Queue) -> None:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                await q.put({"type": kind, "data": chunk.decode(errors="replace")})
            await q.put(None)

        q: asyncio.Queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(_pump(proc.stdout, "stdout", q)),
            asyncio.create_task(_pump(proc.stderr, "stderr", q)),
        ]

        sentinels = 0
        timed_out = False
        deadline = asyncio.get_event_loop().time() + timeout_run

        while sentinels < 2:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if item is None:
                sentinels += 1
            else:
                yield item

        if timed_out:
            proc.kill()
            for t in tasks:
                t.cancel()
            yield {
                "type": "stderr",
                "data": f"\nExecution timed out after {timeout_run}s.\n",
            }
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            yield {"type": "done", "exit_code": 124, "elapsed_ms": elapsed_ms}
            return

        for t in tasks:
            await t
        await proc.wait()

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        yield {"type": "done", "exit_code": proc.returncode or 0, "elapsed_ms": elapsed_ms}

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
