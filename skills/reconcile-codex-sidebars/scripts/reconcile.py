#!/usr/bin/env python3
"""Audit and safely reconcile Codex Desktop sidebar metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
DEFAULT_STATE = CODEX_HOME / ".codex-global-state.json"
DESKTOP_PROCESSES = {
    "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
    "/Applications/Codex.app/Contents/MacOS/Codex",
}
SIDEBAR_KEYS = (
    "local-projects",
    "project-order",
    "thread-project-assignments",
    "sidebar-project-thread-orders",
    "projectless-thread-ids",
)


def alphabetical_key(value: object) -> str:
    """Return the case-insensitive lexical key observed in Codex Remote."""
    return str(value).casefold()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}-", delete=False
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def sidebar_snapshot(state: dict[str, object]) -> dict[str, object]:
    defaults: dict[str, object] = {
        "local-projects": {},
        "project-order": [],
        "thread-project-assignments": {},
        "sidebar-project-thread-orders": {},
        "projectless-thread-ids": [],
    }
    return {key: state.get(key, defaults[key]) for key in SIDEBAR_KEYS}


def validate_state(state: dict[str, object]) -> None:
    expected = {
        "local-projects": dict,
        "project-order": list,
        "thread-project-assignments": dict,
        "sidebar-project-thread-orders": dict,
        "projectless-thread-ids": list,
    }
    errors = [
        f"{key} must be {kind.__name__}"
        for key, kind in expected.items()
        if not isinstance(state.get(key), kind)
    ]
    if errors:
        raise ValueError("unsupported Desktop state contract: " + "; ".join(errors))


def discover_codex(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("CODEX_EXECUTABLE"):
        candidates.append(Path(os.environ["CODEX_EXECUTABLE"]))
    candidates.extend(
        [
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path("/Applications/Codex.app/Contents/Resources/codex"),
        ]
    )
    resolved = shutil.which("codex")
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "could not find the Codex executable; pass --codex or set CODEX_EXECUTABLE"
    )


def desktop_running() -> bool:
    if platform.system() != "Darwin":
        raise RuntimeError(
            "the live Desktop process guard currently supports macOS only"
        )
    result = subprocess.run(
        ["ps", "ax", "-o", "command="],
        check=True,
        capture_output=True,
        text=True,
    )
    return any(
        line.strip().split(" ", 1)[0] in DESKTOP_PROCESSES
        for line in result.stdout.splitlines()
        if line.strip()
    )


class AppServer:
    def __init__(self, executable: Path) -> None:
        self._next_id = 1
        self._process = subprocess.Popen(
            [str(executable), "-c", "features.code_mode_host=true", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.call(
            "initialize",
            {
                "clientInfo": {
                    "name": "reconcile-codex-sidebars",
                    "version": "1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized", {})

    def notify(self, method: str, params: dict[str, object]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(
            json.dumps({"method": method, "params": params}) + "\n"
        )
        self._process.stdin.flush()

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        self._process.stdin.write(
            json.dumps({"method": method, "id": request_id, "params": params}) + "\n"
        )
        self._process.stdin.flush()
        deadline = time.time() + 30
        while time.time() < deadline:
            ready, _, _ = select.select([self._process.stdout], [], [], 0.5)
            if not ready:
                continue
            line = self._process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise TypeError(f"{method}: response result is not an object")
            return result
        raise TimeoutError(method)

    def list_threads(self, archived: bool) -> list[dict[str, object]]:
        threads: list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            params: dict[str, object] = {
                "limit": 100,
                "archived": archived,
                "sortKey": "updated_at",
                "useStateDbOnly": True,
            }
            if cursor:
                params["cursor"] = cursor
            result = self.call("thread/list", params)
            data = result.get("data")
            if not isinstance(data, list):
                raise TypeError("thread/list response has no data array")
            threads.extend(item for item in data if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return threads

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()


def load_threads(
    threads_json: Path | None, codex: Path | None, archived: bool = False
) -> list[dict[str, object]]:
    if threads_json:
        value = json.loads(threads_json.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise ValueError("--threads-json must contain an array of thread objects")
        return value
    server = AppServer(discover_codex(codex))
    try:
        return server.list_threads(archived)
    finally:
        server.close()


def stable_project_id(root: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"codex-local-project:{root}"))


def normalized(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def root_matches(cwd: str, root: str) -> bool:
    try:
        return os.path.commonpath([normalized(cwd), normalized(root)]) == normalized(
            root
        )
    except ValueError:
        return False


def project_by_name(
    projects: dict[str, dict[str, object]], name: str
) -> tuple[str, dict[str, object]]:
    matches = [
        (project_id, project)
        for project_id, project in projects.items()
        if project.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one project named {name!r}, found {len(matches)}")
    return matches[0]


def rule_matches(rule: dict[str, object], thread: dict[str, object]) -> bool:
    if "threadId" in rule and rule["threadId"] != thread.get("id"):
        return False
    if "threadName" in rule and rule["threadName"] != thread.get("name"):
        return False
    if "cwd" in rule and normalized(str(rule["cwd"])) != normalized(
        str(thread.get("cwd") or "")
    ):
        return False
    return "threadId" in rule or "threadName" in rule


def make_plan(
    state_bytes: bytes,
    state: dict[str, object],
    spec: dict[str, object],
    threads: list[dict[str, object]],
    state_path: Path,
    now_ms: int | None = None,
) -> dict[str, object]:
    validate_state(state)
    projects = {
        str(key): dict(value)
        for key, value in state["local-projects"].items()
        if isinstance(value, dict)
    }
    if len(projects) != len(state["local-projects"]):
        raise ValueError("every local project must be an object")
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    new_project_names: list[str] = []
    for item in spec.get("newProjects", []):
        if not isinstance(item, dict):
            raise TypeError("newProjects entries must be objects")
        name = str(item["name"])
        roots = [str(root) for root in item.get("rootPaths", [])]
        if not roots:
            raise ValueError(f"new project {name!r} requires at least one rootPath")
        if any(project.get("name") == name for project in projects.values()):
            continue
        project_id = stable_project_id(roots[0])
        if project_id in projects:
            raise ValueError(f"stable project id collision for {name!r}")
        projects[project_id] = {
            "id": project_id,
            "name": name,
            "rootPaths": roots,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        new_project_names.append(name)

    updated_project_names: list[str] = []
    for item in spec.get("updateProjects", []):
        if not isinstance(item, dict):
            raise TypeError("updateProjects entries must be objects")
        name = str(item["name"])
        roots = [str(root) for root in item.get("rootPaths", [])]
        if not roots:
            raise ValueError(f"updated project {name!r} requires at least one rootPath")
        project_id, project = project_by_name(projects, name)
        project["rootPaths"] = roots
        project["updatedAt"] = timestamp
        projects[project_id] = project
        updated_project_names.append(name)

    raw_rules = spec.get("explicitAssignments", [])
    if not isinstance(raw_rules, list) or not all(
        isinstance(rule, dict) for rule in raw_rules
    ):
        raise ValueError("explicitAssignments must be an array of objects")
    rules: list[dict[str, object]] = raw_rules
    assignments = {
        str(key): dict(value)
        for key, value in state["thread-project-assignments"].items()
        if isinstance(value, dict)
    }
    projectless = {str(item) for item in state["projectless-thread-ids"]}
    added: dict[str, dict[str, str]] = {}
    changed: dict[str, dict[str, object]] = {}
    unmatched: list[dict[str, object]] = []
    ambiguous: list[dict[str, object]] = []

    for thread in threads:
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise ValueError("every thread requires a non-empty id")
        explicit = [rule for rule in rules if rule_matches(rule, thread)]
        if len(explicit) > 1:
            raise ValueError(f"multiple explicit rules match thread {thread_id}")
        rule = explicit[0] if explicit else None
        if thread_id in projectless and not (rule and rule.get("overrideProjectless")):
            continue
        if thread_id in assignments and not (rule and rule.get("reassignExisting")):
            continue
        if rule:
            project_id, _ = project_by_name(projects, str(rule["projectName"]))
        else:
            cwd = str(thread.get("cwd") or "")
            candidates: list[tuple[int, str]] = []
            for candidate_id, project in projects.items():
                for root in project.get("rootPaths", []):
                    root_text = str(root)
                    if root_matches(cwd, root_text):
                        candidates.append((len(normalized(root_text)), candidate_id))
            if not candidates:
                unmatched.append(
                    {"id": thread_id, "name": thread.get("name"), "cwd": cwd}
                )
                continue
            longest = max(length for length, _ in candidates)
            best = sorted(
                {
                    candidate_id
                    for length, candidate_id in candidates
                    if length == longest
                }
            )
            if len(best) != 1:
                ambiguous.append(
                    {
                        "id": thread_id,
                        "name": thread.get("name"),
                        "cwd": cwd,
                        "projectIds": best,
                    }
                )
                continue
            project_id = best[0]
        assignment = {"projectKind": "local", "projectId": project_id}
        previous = assignments.get(thread_id)
        assignments[thread_id] = assignment
        if previous is None:
            added[thread_id] = assignment
        elif previous != assignment:
            changed[thread_id] = {"from": previous, "to": assignment}
        projectless.discard(thread_id)

    thread_by_id = {str(thread["id"]): thread for thread in threads}
    active_ids = set(thread_by_id)
    per_project: dict[str, list[str]] = {project_id: [] for project_id in projects}
    for thread_id, assignment in assignments.items():
        if thread_id not in active_ids or assignment.get("projectKind") != "local":
            continue
        project_id = assignment.get("projectId")
        if project_id in per_project:
            per_project[project_id].append(thread_id)

    def thread_label(thread_id: str) -> tuple[str, str]:
        thread = thread_by_id[thread_id]
        label = thread.get("name") or thread.get("preview") or thread_id
        return alphabetical_key(label), thread_id

    orders = {
        project_id: {"threadIds": sorted(thread_ids, key=thread_label)}
        for project_id, thread_ids in per_project.items()
        if thread_ids
    }
    project_order = sorted(
        projects,
        key=lambda project_id: (
            alphabetical_key(projects[project_id]["name"]),
            project_id,
        ),
    )
    proposed = dict(state)
    proposed["local-projects"] = projects
    proposed["project-order"] = project_order
    proposed["thread-project-assignments"] = assignments
    proposed["sidebar-project-thread-orders"] = orders
    proposed["projectless-thread-ids"] = sorted(projectless)
    return {
        "format": "codex-sidebar-reconcile-plan/v1",
        "createdAt": int(time.time()),
        "statePath": str(state_path),
        "expectedStateSha256": sha256(state_bytes),
        "expectedSidebarSha256": sha256(canonical_json(sidebar_snapshot(state))),
        "proposedStateSha256": sha256(canonical_json(proposed)),
        "proposedSidebarSha256": sha256(canonical_json(sidebar_snapshot(proposed))),
        "activeThreadCount": len(threads),
        "newAssignmentCount": len(added),
        "changedAssignmentCount": len(changed),
        "newAssignments": added,
        "changedAssignments": changed,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "newProjects": sorted(new_project_names, key=alphabetical_key),
        "updatedProjects": sorted(updated_project_names, key=alphabetical_key),
        "projectOrder": [projects[project_id]["name"] for project_id in project_order],
        "proposedState": proposed,
    }


def audit_plan_data(
    plan: dict[str, object], threads: list[dict[str, object]]
) -> dict[str, object]:
    state = plan["proposedState"]
    validate_state(state)
    projects = state["local-projects"]
    assignments = state["thread-project-assignments"]
    projectless = set(state["projectless-thread-ids"])
    active_ids = {thread["id"] for thread in threads}
    assigned_active = active_ids.intersection(assignments)
    projectless_active = active_ids.intersection(projectless)
    overlap = assigned_active.intersection(projectless_active)
    missing = active_ids.difference(assigned_active, projectless_active)
    invalid_projects = {
        thread_id: assignment.get("projectId")
        for thread_id, assignment in assignments.items()
        if assignment.get("projectKind") == "local"
        and assignment.get("projectId") not in projects
    }
    project_order = state["project-order"]
    names = [projects[project_id]["name"] for project_id in project_order]
    alphabetical = names == sorted(names, key=alphabetical_key)
    complete_order = len(project_order) == len(set(project_order)) == len(
        projects
    ) and set(project_order) == set(projects)
    thread_by_id = {thread["id"]: thread for thread in threads}

    def label(thread_id: str) -> tuple[str, str]:
        thread = thread_by_id[thread_id]
        value = thread.get("name") or thread.get("preview") or thread_id
        return alphabetical_key(value), str(thread_id)

    order_errors: list[str] = []
    orders = state["sidebar-project-thread-orders"]
    for project_id, project in projects.items():
        expected = sorted(
            [
                thread_id
                for thread_id in assigned_active
                if assignments[thread_id].get("projectKind") == "local"
                and assignments[thread_id].get("projectId") == project_id
            ],
            key=label,
        )
        actual = orders.get(project_id, {}).get("threadIds", [])
        actual_labels = [label(thread_id)[0] for thread_id in actual]
        expected_labels = [label(thread_id)[0] for thread_id in expected]
        if set(actual) != set(expected) or actual_labels != expected_labels:
            order_errors.append(str(project.get("name") or project_id))
    result = {
        "activeThreads": len(active_ids),
        "assignedActive": len(assigned_active),
        "projectlessActive": len(projectless_active),
        "missing": len(missing),
        "overlap": len(overlap),
        "invalidProjects": len(invalid_projects),
        "projects": len(projects),
        "projectOrderAlphabetical": alphabetical,
        "projectOrderComplete": complete_order,
        "threadOrderErrors": order_errors,
        "archivedAssignmentsPreserved": len(set(assignments).difference(active_ids)),
    }
    if (
        missing
        or overlap
        or invalid_projects
        or not alphabetical
        or not complete_order
        or order_errors
        or plan.get("unmatched")
        or plan.get("ambiguous")
    ):
        raise RuntimeError("plan audit failed: " + json.dumps(result, sort_keys=True))
    return result


def inventory(args: argparse.Namespace) -> None:
    state = load_json(args.state)
    validate_state(state)
    active = load_threads(args.threads_json, args.codex, False)
    archived = [] if args.threads_json else load_threads(None, args.codex, True)
    active_ids = {thread["id"] for thread in active}
    assignments = state["thread-project-assignments"]
    projectless = set(state["projectless-thread-ids"])
    summary = {
        "activeThreads": len(active),
        "archivedThreads": len(archived) if archived else None,
        "projects": len(state["local-projects"]),
        "assignedActive": len(active_ids.intersection(assignments)),
        "projectlessActive": len(active_ids.intersection(projectless)),
        "unrepresentedActive": len(
            active_ids.difference(assignments).difference(projectless)
        ),
    }
    if args.output:
        detailed = {
            "format": "codex-sidebar-inventory/v1",
            "createdAt": int(time.time()),
            "summary": summary,
            "activeThreads": active,
            "archivedThreads": archived,
            "sidebarState": sidebar_snapshot(state),
        }
        write_atomic(args.output, canonical_json(detailed))
        summary["privateOutput"] = str(args.output)
    print(json.dumps(summary, indent=2))


def build_plan(args: argparse.Namespace) -> None:
    state_bytes = args.state.read_bytes()
    state = json.loads(state_bytes)
    if not isinstance(state, dict):
        raise TypeError("Desktop state must be a JSON object")
    spec = load_json(args.spec)
    threads = load_threads(args.threads_json, args.codex, False)
    plan = make_plan(state_bytes, state, spec, threads, args.state)
    write_atomic(args.output, canonical_json(plan))
    print(
        json.dumps(
            {
                "privatePlan": str(args.output),
                "activeThreads": plan["activeThreadCount"],
                "newAssignments": plan["newAssignmentCount"],
                "changedAssignments": plan["changedAssignmentCount"],
                "unmatched": len(plan["unmatched"]),
                "ambiguous": len(plan["ambiguous"]),
                "projects": len(plan["proposedState"]["local-projects"]),
                "newProjects": plan["newProjects"],
                "updatedProjects": plan["updatedProjects"],
            },
            indent=2,
        )
    )


def audit_plan(args: argparse.Namespace) -> None:
    plan = load_json(args.plan)
    threads = load_threads(args.threads_json, args.codex, False)
    result = audit_plan_data(plan, threads)
    result["audited"] = True
    print(json.dumps(result, indent=2))


def validate_plan_for_apply(plan: dict[str, object]) -> tuple[Path, bytes]:
    if plan.get("format") != "codex-sidebar-reconcile-plan/v1":
        raise ValueError("unsupported plan format")
    if plan.get("unmatched") or plan.get("ambiguous"):
        raise RuntimeError("refusing a plan with unmatched or ambiguous active threads")
    state_path = Path(plan["statePath"])
    current = load_json(state_path)
    validate_state(current)
    current_sidebar_hash = sha256(canonical_json(sidebar_snapshot(current)))
    if current_sidebar_hash != plan.get("expectedSidebarSha256"):
        raise RuntimeError(
            "Codex sidebar state changed; rebuild the plan instead of applying stale data"
        )
    proposed_state = plan["proposedState"]
    proposed_payload = canonical_json(proposed_state)
    if sha256(proposed_payload) != plan["proposedStateSha256"]:
        raise RuntimeError("plan payload hash does not match")
    merged = dict(current)
    for key in SIDEBAR_KEYS:
        merged[key] = proposed_state[key]
    merged_sidebar_hash = sha256(canonical_json(sidebar_snapshot(merged)))
    if merged_sidebar_hash != plan["proposedSidebarSha256"]:
        raise RuntimeError("merged sidebar payload hash does not match")
    return state_path, canonical_json(merged)


def apply_plan_data(plan_path: Path) -> dict[str, object]:
    if desktop_running():
        raise RuntimeError("Codex Desktop is running; quit it before applying the plan")
    plan = load_json(plan_path)
    state_path, proposed = validate_plan_for_apply(plan)
    backup_dir = state_path.parent / "backups" / "sidebar-reconcile"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{time.time_ns()}-{state_path.name}"
    shutil.copy2(state_path, backup)
    with backup.open("rb") as stream:
        os.fsync(stream.fileno())
    write_atomic(state_path, proposed, state_path.stat().st_mode & 0o777)
    written = load_json(state_path)
    sidebar_hash = sha256(canonical_json(sidebar_snapshot(written)))
    if sidebar_hash != plan["proposedSidebarSha256"]:
        raise RuntimeError("written sidebar state failed verification")
    return {
        "applied": True,
        "verified": True,
        "backup": str(backup),
        "statePath": str(state_path),
        "sidebarSha256": sidebar_hash,
    }


def apply_plan(args: argparse.Namespace) -> None:
    print(json.dumps(apply_plan_data(args.plan), indent=2))


def verify_state_data(plan_path: Path) -> dict[str, object]:
    plan = load_json(plan_path)
    state_path = Path(plan["statePath"])
    state = load_json(state_path)
    validate_state(state)
    projects = state["local-projects"]
    names = [projects[project_id]["name"] for project_id in state["project-order"]]
    if names != sorted(names, key=alphabetical_key):
        raise RuntimeError("project order is not alphabetical")
    sidebar_hash = sha256(canonical_json(sidebar_snapshot(state)))
    if sidebar_hash != plan["proposedSidebarSha256"]:
        raise RuntimeError("live sidebar state does not match the proposed state")
    return {
        "verified": True,
        "projects": len(projects),
        "assignments": len(state["thread-project-assignments"]),
        "sidebarSha256": sidebar_hash,
    }


def verify_state(args: argparse.Namespace) -> None:
    print(json.dumps(verify_state_data(args.plan), indent=2))


def relaunch_desktop() -> None:
    for app_name in ("ChatGPT", "Codex"):
        for command in (["open", "-a", app_name], ["open", "-n", "-a", app_name]):
            result = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            if result.returncode != 0:
                continue
            deadline = time.time() + 15
            while time.time() < deadline:
                if desktop_running():
                    return
                time.sleep(0.5)
    raise RuntimeError("could not relaunch Codex Desktop")


def watch_apply(args: argparse.Namespace) -> None:
    deadline = time.time() + args.timeout
    result: dict[str, object]
    try:
        while desktop_running():
            if time.time() >= deadline:
                raise TimeoutError("Codex Desktop did not exit before the deadline")
            time.sleep(0.5)
        time.sleep(1.0)
        plan = load_json(args.plan)
        audit_plan_data(plan, load_threads(None, None, False))
        result = apply_plan_data(args.plan)
        result["completedAt"] = int(time.time())
    except Exception as error:  # noqa: BLE001 - persist any failure before relaunch
        result = {
            "applied": False,
            "verified": False,
            "failedAt": int(time.time()),
            "error": f"{type(error).__name__}: {error}",
        }
    write_atomic(args.receipt, canonical_json(result))
    print(json.dumps(result, indent=2), flush=True)
    if args.relaunch:
        relaunch_desktop()
    if not result["verified"]:
        raise RuntimeError(str(result["error"]))


def arm_macos_restart(args: argparse.Namespace) -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("arm-macos-restart requires macOS")
    if not desktop_running():
        raise RuntimeError("Codex Desktop is not running; use apply-plan directly")
    plan = load_json(args.plan)
    validate_plan_for_apply(plan)
    audit_plan_data(plan, load_threads(None, None, False))
    runtime_root = args.runtime_root.expanduser().resolve()
    expected_root = (CODEX_HOME / "sidebar-reconcile-runtime").resolve()
    if runtime_root != expected_root:
        raise ValueError(f"runtime root must be exactly {expected_root}")
    case_id = uuid.uuid4().hex
    case_root = runtime_root / case_id
    case_root.mkdir(parents=True, mode=0o700)
    runtime_script = case_root / "reconcile.py"
    runtime_plan = case_root / "plan.json"
    shutil.copy2(Path(__file__).resolve(), runtime_script)
    shutil.copy2(args.plan, runtime_plan)
    os.chmod(runtime_script, 0o700)
    os.chmod(runtime_plan, 0o600)
    receipt = case_root / "receipt.json"
    log = case_root / "watch.log"
    label = f"com.openai.codex.sidebar-reconcile.{case_id}"
    plist_path = case_root / f"{label}.plist"
    interpreter = Path("/usr/bin/python3")
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(interpreter),
            str(runtime_script),
            "watch-apply",
            "--plan",
            str(runtime_plan),
            "--receipt",
            str(receipt),
            "--timeout",
            str(args.timeout),
            "--relaunch",
        ],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    write_atomic(plist_path, plistlib.dumps(plist))
    manifest = {
        "format": "codex-sidebar-restart-manifest/v1",
        "label": label,
        "caseRoot": str(case_root),
        "plan": str(runtime_plan),
        "receipt": str(receipt),
        "log": str(log),
        "plist": str(plist_path),
    }
    manifest_path = case_root / "manifest.json"
    write_atomic(manifest_path, canonical_json(manifest))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    time.sleep(0.5)
    status = subprocess.run(
        ["launchctl", "print", f"{domain}/{label}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or "state = running" not in status.stdout:
        raise RuntimeError("launchd watcher did not enter the running state")
    print(
        json.dumps(
            {
                "armed": True,
                "manifest": str(manifest_path),
                "receipt": str(receipt),
                "log": str(log),
                "next": "Quit Codex Desktop normally; it will relaunch automatically.",
            },
            indent=2,
        )
    )


def check_macos_restart(args: argparse.Namespace) -> None:
    manifest = load_json(args.manifest)
    if manifest.get("format") != "codex-sidebar-restart-manifest/v1":
        raise ValueError("unsupported restart manifest")
    case_root = Path(manifest["caseRoot"]).resolve()
    runtime_root = (CODEX_HOME / "sidebar-reconcile-runtime").resolve()
    if case_root.parent != runtime_root:
        raise ValueError("manifest caseRoot is outside the private runtime root")
    receipt_path = Path(manifest["receipt"])
    if not receipt_path.exists():
        raise RuntimeError("restart receipt is not available yet")
    receipt = load_json(receipt_path)
    if not receipt.get("applied") or not receipt.get("verified"):
        raise RuntimeError(
            "restart apply failed: " + json.dumps(receipt, sort_keys=True)
        )
    verification = verify_state_data(Path(manifest["plan"]))
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{manifest['label']}"],
        check=False,
        capture_output=True,
        text=True,
    )
    result = {
        "restartVerified": True,
        "receipt": receipt,
        "state": verification,
        "privateRuntime": str(case_root),
    }
    if args.purge_private_runtime:
        shutil.rmtree(case_root)
        result["privateRuntimePurged"] = True
    print(json.dumps(result, indent=2))


def disarm_macos_restart(args: argparse.Namespace) -> None:
    manifest = load_json(args.manifest)
    if manifest.get("format") != "codex-sidebar-restart-manifest/v1":
        raise ValueError("unsupported restart manifest")
    case_root = Path(manifest["caseRoot"]).resolve()
    runtime_root = (CODEX_HOME / "sidebar-reconcile-runtime").resolve()
    if case_root.parent != runtime_root:
        raise ValueError("manifest caseRoot is outside the private runtime root")
    receipt_path = Path(manifest["receipt"])
    if receipt_path.exists() and load_json(receipt_path).get("applied"):
        raise RuntimeError("the plan was already applied; use check-macos-restart")
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{manifest['label']}"],
        check=False,
        capture_output=True,
        text=True,
    )
    shutil.rmtree(case_root)
    print(json.dumps({"disarmed": True, "privateRuntimePurged": True}, indent=2))


def add_common_thread_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--threads-json", type=Path)
    parser.add_argument("--codex", type=Path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    inventory_parser.add_argument("--output", type=Path)
    add_common_thread_args(inventory_parser)
    inventory_parser.set_defaults(func=inventory)

    build = subparsers.add_parser("build-plan")
    build.add_argument("--state", type=Path, default=DEFAULT_STATE)
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    add_common_thread_args(build)
    build.set_defaults(func=build_plan)

    audit = subparsers.add_parser("audit-plan")
    audit.add_argument("--plan", type=Path, required=True)
    add_common_thread_args(audit)
    audit.set_defaults(func=audit_plan)

    apply = subparsers.add_parser("apply-plan")
    apply.add_argument("--plan", type=Path, required=True)
    apply.set_defaults(func=apply_plan)

    verify = subparsers.add_parser("verify-state")
    verify.add_argument("--plan", type=Path, required=True)
    verify.set_defaults(func=verify_state)

    watch = subparsers.add_parser("watch-apply")
    watch.add_argument("--plan", type=Path, required=True)
    watch.add_argument("--receipt", type=Path, required=True)
    watch.add_argument("--timeout", type=int, default=3600)
    watch.add_argument("--relaunch", action="store_true")
    watch.set_defaults(func=watch_apply)

    arm = subparsers.add_parser("arm-macos-restart")
    arm.add_argument("--plan", type=Path, required=True)
    arm.add_argument(
        "--runtime-root",
        type=Path,
        default=CODEX_HOME / "sidebar-reconcile-runtime",
    )
    arm.add_argument("--timeout", type=int, default=3600)
    arm.set_defaults(func=arm_macos_restart)

    check = subparsers.add_parser("check-macos-restart")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--purge-private-runtime", action="store_true")
    check.set_defaults(func=check_macos_restart)

    disarm = subparsers.add_parser("disarm-macos-restart")
    disarm.add_argument("--manifest", type=Path, required=True)
    disarm.set_defaults(func=disarm_macos_restart)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.func(arguments)
