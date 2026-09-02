#!/usr/bin/env python3
"""Synchronize Desktop's reviewed project membership into App Server projects."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "reconcile_for_projects", SCRIPTS / "reconcile.py"
)
assert _SPEC and _SPEC.loader
reconcile = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reconcile)


def list_projects(server: object) -> list[dict[str, object]]:
    projects: list[dict[str, object]] = []
    cursor: str | None = None
    while True:
        params: dict[str, object] = {
            "limit": 100,
            "sortKey": "position",
            "sortDirection": "asc",
        }
        if cursor:
            params["cursor"] = cursor
        result = server.call("project/list", params)
        data = result.get("data")
        if not isinstance(data, list):
            raise TypeError("project/list response has no data array")
        projects.extend(item for item in data if isinstance(item, dict))
        cursor = result.get("nextCursor")
        if not cursor:
            return projects


def _migration_map(
    state: dict[str, object], server_projects: list[dict[str, object]]
) -> dict[str, str]:
    server_ids = {
        str(project["id"])
        for project in server_projects
        if isinstance(project.get("id"), str)
    }
    by_legacy: dict[str, str] = {}
    host_maps = state.get("app-server-project-id-by-legacy-project-id-by-host", {})
    if isinstance(host_maps, dict):
        for host_map in host_maps.values():
            if not isinstance(host_map, dict):
                continue
            for legacy_id, server_id in host_map.items():
                if isinstance(server_id, str) and server_id in server_ids:
                    by_legacy[str(legacy_id)] = server_id

    local_projects = state.get("local-projects", {})
    if not isinstance(local_projects, dict):
        raise TypeError("local-projects must be an object")

    def identity(
        project: dict[str, object], roots_key: str
    ) -> tuple[str, tuple[str, ...]]:
        roots = project.get(roots_key, [])
        paths: list[str] = []
        if isinstance(roots, list):
            for root in roots:
                if isinstance(root, str):
                    paths.append(reconcile.normalized(root))
                elif isinstance(root, dict) and isinstance(root.get("path"), str):
                    paths.append(reconcile.normalized(str(root["path"])))
        return str(project.get("name", "")), tuple(sorted(paths))

    server_by_identity: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for project in server_projects:
        key = identity(project, "roots")
        server_by_identity.setdefault(key, []).append(str(project["id"]))

    for legacy_id, project in local_projects.items():
        if str(legacy_id) in by_legacy or not isinstance(project, dict):
            continue
        matches = server_by_identity.get(identity(project, "rootPaths"), [])
        if len(matches) == 1:
            by_legacy[str(legacy_id)] = matches[0]
    return by_legacy


def make_plan(
    state_bytes: bytes,
    state: dict[str, object],
    server_projects: list[dict[str, object]],
    threads: list[dict[str, object]],
    state_path: Path,
) -> dict[str, object]:
    reconcile.validate_state(state)
    mapping = _migration_map(state, server_projects)
    server_by_name: dict[str, list[str]] = {}
    for project in server_projects:
        server_by_name.setdefault(str(project.get("name", "")), []).append(
            str(project["id"])
        )
    assignments = state["thread-project-assignments"]
    projectless = {str(item) for item in state["projectless-thread-ids"]}
    assert isinstance(assignments, dict)

    changes: list[dict[str, object]] = []
    missing: list[str] = []
    for thread in threads:
        thread_id = str(thread.get("id", ""))
        local_assignment = assignments.get(thread_id)
        if isinstance(local_assignment, dict):
            legacy_id = str(local_assignment.get("projectId", ""))
            after = mapping.get(legacy_id)
            if not after:
                missing.append(thread_id)
                continue
        elif thread_id in projectless:
            after = None
        else:
            section = thread.get("section")
            section_name = (
                str(section.get("name", "")) if isinstance(section, dict) else ""
            )
            section_matches = server_by_name.get(section_name, [])
            if section_name != "Pinned" and len(section_matches) == 1:
                after = section_matches[0]
            else:
                missing.append(thread_id)
                continue
        before = thread.get("projectId")
        if before != after:
            changes.append(
                {
                    "threadId": thread_id,
                    "label": thread.get("name")
                    or thread.get("preview")
                    or "Untitled",
                    "beforeProjectId": before,
                    "afterProjectId": after,
                }
            )

    if missing:
        raise ValueError(
            f"{len(missing)} active threads are not classified in Desktop metadata"
        )

    desired_projects = sorted(
        server_projects, key=lambda item: reconcile.natural_key(item["name"])
    )
    return {
        "schemaVersion": 1,
        "statePath": str(state_path),
        "stateSha256": reconcile.sha256(state_bytes),
        "activeThreads": len(threads),
        "assignmentChanges": changes,
        "projectOrderBefore": [str(item["id"]) for item in server_projects],
        "projectOrderAfter": [str(item["id"]) for item in desired_projects],
        "projectNamesAfter": [str(item["name"]) for item in desired_projects],
    }


def verify_plan(plan: dict[str, object], server: object) -> dict[str, object]:
    threads = {str(item["id"]): item for item in server.list_threads(False)}
    projects = list_projects(server)
    wrong: list[str] = []
    for change in plan["assignmentChanges"]:
        thread = threads.get(str(change["threadId"]))
        if thread is None or thread.get("projectId") != change["afterProjectId"]:
            wrong.append(str(change["threadId"]))
    actual_order = [str(item["id"]) for item in projects]
    expected_order = [str(item) for item in plan["projectOrderAfter"]]
    return {
        "verified": not wrong and actual_order == expected_order,
        "activeThreads": len(threads),
        "assignmentChanges": len(plan["assignmentChanges"]),
        "wrongAssignments": wrong,
        "projectOrderAlphabetical": actual_order == expected_order,
    }


def move_project_order(server: object, project_ids: list[str]) -> None:
    successor: str | None = None
    for project_id in reversed(project_ids):
        server.call(
            "project/move",
            {"projectId": project_id, "beforeProjectId": successor},
        )
        successor = project_id


def apply_plan_data(plan: dict[str, object], server: object) -> dict[str, object]:
    current = {str(item["id"]): item for item in server.list_threads(False)}
    for change in plan["assignmentChanges"]:
        thread = current.get(str(change["threadId"]))
        if thread is None or thread.get("projectId") != change["beforeProjectId"]:
            raise RuntimeError("server thread membership changed after plan creation")

    projects = list_projects(server)
    current_order = [str(item["id"]) for item in projects]
    if current_order != plan["projectOrderBefore"]:
        raise RuntimeError("server project order changed after plan creation")

    applied: list[dict[str, object]] = []
    try:
        for change in plan["assignmentChanges"]:
            server.call(
                "thread/metadata/update",
                {
                    "threadId": change["threadId"],
                    "projectId": change["afterProjectId"] or "",
                },
            )
            applied.append(change)
        desired = [str(item) for item in plan["projectOrderAfter"]]
        if current_order != desired:
            move_project_order(server, desired)
    except Exception:
        for change in reversed(applied):
            server.call(
                "thread/metadata/update",
                {
                    "threadId": change["threadId"],
                    "projectId": change["beforeProjectId"] or "",
                },
            )
        if [str(item["id"]) for item in list_projects(server)] != current_order:
            move_project_order(server, current_order)
        raise

    verification = verify_plan(plan, server)
    if not verification["verified"]:
        raise RuntimeError("post-apply project synchronization verification failed")
    return {"applied": len(applied), "verification": verification}


def rollback_data(receipt: dict[str, object], server: object) -> dict[str, object]:
    plan = receipt["plan"]
    threads = {str(item["id"]): item for item in server.list_threads(False)}
    current_order = [str(item["id"]) for item in list_projects(server)]
    if current_order != plan["projectOrderAfter"]:
        raise RuntimeError("refusing rollback because server project order changed")
    for change in plan["assignmentChanges"]:
        thread = threads.get(str(change["threadId"]))
        if thread is None or thread.get("projectId") != change["afterProjectId"]:
            raise RuntimeError("refusing rollback because server membership changed")
    for change in reversed(plan["assignmentChanges"]):
        server.call(
            "thread/metadata/update",
            {
                "threadId": change["threadId"],
                "projectId": change["beforeProjectId"] or "",
            },
        )
    if current_order != plan["projectOrderBefore"]:
        move_project_order(
            server, [str(item) for item in plan["projectOrderBefore"]]
        )
    return {"restored": len(plan["assignmentChanges"])}


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("build-plan", "apply-plan", "verify", "rollback")
    )
    parser.add_argument("--state", type=Path, default=reconcile.DEFAULT_STATE)
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    server = reconcile.AppServer(reconcile.discover_codex(args.codex))
    try:
        if args.command == "build-plan":
            state_bytes = args.state.read_bytes()
            state = json.loads(state_bytes)
            result = make_plan(
                state_bytes,
                state,
                list_projects(server),
                server.list_threads(False),
                args.state,
            )
        elif args.command == "apply-plan":
            if not args.plan or not args.receipt:
                parser.error("apply-plan requires --plan and --receipt")
            plan = read_object(args.plan)
            state_path = Path(str(plan["statePath"]))
            if reconcile.sha256(state_path.read_bytes()) != plan["stateSha256"]:
                raise RuntimeError("Desktop state changed after plan creation")
            result = apply_plan_data(plan, server)
            receipt = {"schemaVersion": 1, "plan": plan, "result": result}
            reconcile.write_atomic(args.receipt, reconcile.canonical_json(receipt))
        elif args.command == "verify":
            if not args.plan:
                parser.error("verify requires --plan")
            result = verify_plan(read_object(args.plan), server)
        else:
            if not args.receipt:
                parser.error("rollback requires --receipt")
            result = rollback_data(read_object(args.receipt), server)
        payload = reconcile.canonical_json(result)
        if args.output:
            reconcile.write_atomic(args.output, payload)
        else:
            print(payload.decode(), end="")
    finally:
        server.close()


if __name__ == "__main__":
    main()
