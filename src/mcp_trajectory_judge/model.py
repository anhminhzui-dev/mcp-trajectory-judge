"""Loading, and the deterministic sandbox that trajectories are replayed against.

No network, no clock, no model, no randomness: the same task file and the same trajectory
bytes always reach the same end state. That is what makes the verification deterministic
rather than persuasive, and it is why every judgment here can carry a hash of its inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TYPE_NAMES: dict[str, type] = {"str": str, "int": int, "bool": bool}
REQUIRED_TASK_FIELDS = ("task_id", "tools", "files", "tests", "goal", "golden", "step_budget")


class HaltError(Exception):
    """A run-level refusal: the batch stops and the verdict is HOLD, never a silent pass."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(obj: Any) -> str:
    """Key-sorted, separator-fixed JSON: the only form this package hashes or compares."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class Tool:
    """One tool as the manifest declares it, normalised into one shape.

    `properties` is every argument name the tool declares mapped to its type name; `required`
    is the subset that must be present. A name in `properties` but outside `required` is
    optional, which is what a real server manifest means by `limit` or `recursive`.
    """

    name: str
    properties: dict[str, str]
    required: tuple[str, ...]
    destructive: bool


def tool_schema(entry: dict[str, Any]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Read both manifest dialects into that one shape.

    Compact: {"required": {"path": "str"}} - every declared argument is required.
    JSON-Schema style: {"properties": {"path": "str", "recursive": "bool"},
    "required": ["path"]} - the declared names outside the list are optional.
    A required name the manifest never declares halts the run instead of being read as untyped.
    """
    name = str(entry.get("name"))
    declared = entry.get("properties")
    if declared is None:
        required = entry.get("required", {})
        if not isinstance(required, dict):
            raise HaltError("MALFORMED_TOOL_SCHEMA", f"{name}: required list without properties")
        props = {str(k): str(v) for k, v in required.items()}
        return props, tuple(sorted(props))
    required = entry.get("required", [])
    if not isinstance(declared, dict) or not isinstance(required, list):
        raise HaltError("MALFORMED_TOOL_SCHEMA", f"{name}: properties needs a required list")
    props = {str(k): str(v) for k, v in declared.items()}
    undeclared = sorted(str(n) for n in required if str(n) not in props)
    if undeclared:
        raise HaltError("MALFORMED_TOOL_SCHEMA", f"{name}: required not declared: {undeclared}")
    return props, tuple(str(n) for n in required)


@dataclass(frozen=True)
class Task:
    task_id: str
    tools: dict[str, Tool]
    files: dict[str, str]
    tests: dict[str, str]
    goal: tuple[dict[str, Any], ...]
    golden: tuple[dict[str, Any], ...]
    step_budget: int
    sha256: str


def load_task(path: str | Path, allow_nonsynthetic: bool = False) -> Task:
    """Read the server manifest + task file. Anything unreadable halts the run."""
    raw = Path(path).read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HaltError("MALFORMED_TASK_FILE", type(exc).__name__) from exc
    if not isinstance(doc, dict):
        raise HaltError("MALFORMED_TASK_FILE", "top level is not an object")
    if doc.get("synthetic") is not True and not allow_nonsynthetic:
        raise HaltError("NOT_SYNTHETIC", "the task file carries no synthetic marker")
    for name in REQUIRED_TASK_FIELDS:
        if name not in doc:
            raise HaltError("MISSING_TASK_FIELD", name)
    tools: dict[str, Tool] = {}
    for entry in doc["tools"]:
        properties, required = tool_schema(entry)
        tools[entry["name"]] = Tool(
            name=str(entry["name"]),
            properties=properties,
            required=required,
            destructive=bool(entry.get("destructive", False)),
        )
    return Task(
        task_id=str(doc["task_id"]),
        tools=tools,
        files=dict(doc["files"]),
        tests=dict(doc["tests"]),
        goal=tuple(doc["goal"]),
        golden=tuple(doc["golden"]),
        step_budget=int(doc["step_budget"]),
        sha256=sha256_bytes(raw),
    )


class Sandbox:
    """A dict-backed file tree with six tools. Every call returns a plain dict; a failed call
    returns {"error": <code>} and changes nothing. `read` remembers which paths the trajectory
    actually looked at, which is what the destructive-without-read check is built on."""

    def __init__(self, files: dict[str, str], tests: dict[str, str]) -> None:
        self.files: dict[str, str] = dict(files)
        self.tests: dict[str, str] = dict(tests)
        self.read: set[str] = set()

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, "_t_" + tool, None)
        if handler is None:
            return {"error": "NO_SUCH_TOOL"}
        return handler(args)

    def _t_list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = str(args["path"])
        prefix = "" if raw in ("", ".", "./") else raw.rstrip("/") + "/"
        entries = sorted(p for p in self.files if p.startswith(prefix))
        return {"entries": entries} if entries else {"error": "ENOENT"}

    def _t_read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args["path"])
        if path not in self.files:
            return {"error": "ENOENT"}
        self.read.add(path)
        return {"content": self.files[path]}

    def _t_search(self, args: dict[str, Any]) -> dict[str, Any]:
        needle = str(args["pattern"])
        return {"matches": sorted(p for p, body in self.files.items() if needle in body or needle in p)}

    def _t_write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        self.files[str(args["path"])] = str(args["content"])
        return {"written": True}

    def _t_delete_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args["path"])
        if path not in self.files:
            return {"error": "ENOENT"}
        del self.files[path]
        return {"deleted": True}

    def _t_run_tests(self, _args: dict[str, Any]) -> dict[str, Any]:
        ok = tests_pass(self)
        return {"passed": 1 if ok else 0, "failed": 0 if ok else 1}


def tests_pass(sandbox: Sandbox) -> bool:
    """The task's own test oracle, stated in the task file, not inferred from the trajectory."""
    body = sandbox.files.get(str(sandbox.tests["path"]))
    return body is not None and str(sandbox.tests["must_contain"]) in body


def goal_reached(task: Task, sandbox: Sandbox) -> bool:
    """Every assertion in the task file must hold on the replayed end state."""
    for assertion in task.goal:
        kind = assertion.get("kind")
        if kind == "file_contains":
            body = sandbox.files.get(str(assertion["path"]))
            if body is None or str(assertion["substring"]) not in body:
                return False
        elif kind == "file_absent":
            if str(assertion["path"]) in sandbox.files:
                return False
        elif kind == "tests_pass":
            if not tests_pass(sandbox):
                return False
        else:
            raise HaltError("UNKNOWN_GOAL_KIND", str(kind))
    return True
