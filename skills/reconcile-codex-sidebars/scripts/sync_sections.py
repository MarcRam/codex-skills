#!/usr/bin/env python3
"""Mirror Desktop project groupings into synchronized App Server sections."""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

SUPPORT_SCRIPT = Path(__file__).with_name("reconcile.py")
SUPPORT_SPEC = importlib.util.spec_from_file_location("sidebar_reconcile", SUPPORT_SCRIPT)
if SUPPORT_SPEC is None or SUPPORT_SPEC.loader is None:
    raise RuntimeError(f"could not load {SUPPORT_SCRIPT}")
support = importlib.util.module_from_spec(SUPPORT_SPEC)
SUPPORT_SPEC.loader.exec_module(support)


def list_sections(server: support.AppServer) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        result = server.call("threadSection/list", params)
        data = result.get("data")
        if not isinstance(data, list):
            raise TypeError("threadSection/list response has no data array")
        sections.extend(item for item in data if isinstance(item, dict))
        cursor = result.get("nextCursor")
        if not cursor:
            return sections


def thread_section_snapshot(threads: list[dict[str, object]]) -> list[dict[str, object]]:
    snapshot = []
    for thread in threads:
        section = thread.get("section")
        snapshot.append(
            {
                "threadId": str(thread["id"]),
                "sectionId": section.get("id") if isinstance(section, dict) else None,
                "sectionName": section.get("name") if isinstance(section, dict) else None,
            }
        )
    return sorted(snapshot, key=lambda item: item["threadId"])


def server_snapshot(
    sections: list[dict[str, object]], threads: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "sections": sorted(
            [
                {
                    "id": section.get("id"),
                    "name": section.get("name"),
                    "appearance": section.get("appearance"),
                }
                for section in sections
            ],
            key=lambda item: (
                support.alphabetical_key(item["name"]),
                str(item["id"]),
            ),
        ),
        "threads": thread_section_snapshot(threads),
    }


def digest(value: object) -> str:
    return support.sha256(support.canonical_json(value))


def make_section_plan(
    state: dict[str, object],
    active: list[dict[str, object]],
    sections: list[dict[str, object]],
    state_path: Path,
) -> dict[str, object]:
    support.validate_state(state)
    active_by_id = {str(thread["id"]): thread for thread in active}
    active_ids = set(active_by_id)
    assignments = state["thread-project-assignments"]
    projectless = {str(item) for item in state["projectless-thread-ids"]}
    projects = state["local-projects"]
    project_names = {
        str(projects[project_id]["name"]) for project_id in state["project-order"]
    }
    pinned_active = {
        thread_id
        for thread_id, thread in active_by_id.items()
        if isinstance(thread.get("section"), dict)
        and thread["section"].get("name") == "Pinned"
    }
    locally_assigned_active = active_ids.intersection(assignments) - pinned_active
    projectless_active = active_ids.intersection(projectless) - pinned_active
    server_only_by_name: dict[str, set[str]] = {
        name: set() for name in project_names
    }
    for thread_id in active_ids.difference(assignments, projectless, pinned_active):
        section = active_by_id[thread_id].get("section")
        section_name = section.get("name") if isinstance(section, dict) else None
        if section_name in server_only_by_name:
            server_only_by_name[str(section_name)].add(thread_id)
    server_only_active = set().union(*server_only_by_name.values())
    missing = active_ids.difference(
        locally_assigned_active,
        projectless_active,
        pinned_active,
        server_only_active,
    )
    overlap = locally_assigned_active.intersection(projectless_active)
    if missing or overlap:
        raise RuntimeError(
            f"Desktop mapping is incomplete: missing={len(missing)} overlap={len(overlap)}"
        )

    def thread_label(thread_id: str) -> tuple[str, str]:
        thread = active_by_id[thread_id]
        label = thread.get("name") or thread.get("preview") or thread_id
        return support.alphabetical_key(label), thread_id

    planned_sections: list[dict[str, object]] = []
    for project_id in state["project-order"]:
        project = projects[project_id]
        expected = {
            thread_id
            for thread_id in locally_assigned_active
            if assignments[thread_id].get("projectKind") == "local"
            and assignments[thread_id].get("projectId") == project_id
        }
        members = list(expected)
        members.extend(server_only_by_name[str(project["name"])])
        members = sorted(members, key=thread_label)
        planned_sections.append(
            {
                "projectId": project_id,
                "name": str(project["name"]),
                "threadIds": members,
            }
        )

    names = [str(section["name"]) for section in planned_sections]
    if names != sorted(names, key=support.alphabetical_key):
        raise RuntimeError("Desktop project order is not alphabetical")
    existing_names = [str(section.get("name")) for section in sections]
    duplicates = sorted(
        {name for name in existing_names if existing_names.count(name) > 1},
        key=support.alphabetical_key,
    )
    if duplicates:
        raise RuntimeError(f"duplicate server section names: {duplicates}")
    unexpected = sorted(
        set(existing_names).difference({"Pinned"}, set(names)),
        key=support.alphabetical_key,
    )
    if unexpected:
        raise RuntimeError(f"unexpected existing server sections: {unexpected}")

    current_server_state = server_snapshot(sections, active)
    return {
        "format": "codex-sidebar-section-sync-plan/v1",
        "createdAt": int(time.time()),
        "statePath": str(state_path),
        "expectedSidebarSha256": digest(support.sidebar_snapshot(state)),
        "expectedServerSha256": digest(current_server_state),
        "activeThreads": len(active),
        "assignedThreads": len(locally_assigned_active) + len(server_only_active),
        "projectlessThreads": len(projectless_active),
        "pinnedThreads": len(pinned_active),
        "serverOnlyThreads": len(server_only_active),
        "preserveSectionNames": ["Pinned"],
        "sections": planned_sections,
        "projectlessThreadIds": sorted(projectless_active),
        "previousServerState": current_server_state,
    }


def build_plan(args: argparse.Namespace) -> None:
    state = support.load_json(args.state)
    server = support.AppServer(support.discover_codex(args.codex))
    try:
        plan = make_section_plan(
            state, server.list_threads(False), list_sections(server), args.state
        )
    finally:
        server.close()
    support.write_atomic(args.output, support.canonical_json(plan))
    print(
        json.dumps(
            {
                "privatePlan": str(args.output),
                "sections": len(plan["sections"]),
                "assignedThreads": plan["assignedThreads"],
                "projectlessThreads": plan["projectlessThreads"],
            },
            indent=2,
        )
    )


def validate_fresh(
    plan: dict[str, object], server: support.AppServer
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if plan.get("format") != "codex-sidebar-section-sync-plan/v1":
        raise ValueError("unsupported section sync plan")
    state = support.load_json(Path(str(plan["statePath"])))
    if digest(support.sidebar_snapshot(state)) != plan["expectedSidebarSha256"]:
        raise RuntimeError("Desktop sidebar metadata changed; rebuild the plan")
    active = server.list_threads(False)
    sections = list_sections(server)
    if digest(server_snapshot(sections, active)) != plan["expectedServerSha256"]:
        raise RuntimeError("server sections or active tasks changed; rebuild the plan")
    return active, sections


def verify_plan(
    plan: dict[str, object], server: support.AppServer
) -> dict[str, object]:
    active = server.list_threads(False)
    sections = list_sections(server)
    by_thread = {str(thread["id"]): thread for thread in active}
    by_name = {str(section["name"]): section for section in sections}
    planned_names = [str(section["name"]) for section in plan["sections"]]
    planned_name_set = set(planned_names)
    missing_sections = [name for name in planned_names if name not in by_name]
    wrong: list[str] = []
    for planned in plan["sections"]:
        live_section = by_name.get(str(planned["name"]))
        live_id = live_section.get("id") if live_section else None
        for thread_id in planned["threadIds"]:
            thread = by_thread.get(str(thread_id))
            if thread is None:
                continue
            section = thread.get("section") if thread else None
            if (
                not isinstance(section, dict)
                or section.get("id") != live_id
            ) and not (
                isinstance(section, dict) and section.get("name") == "Pinned"
            ):
                wrong.append(str(thread_id))
    projectless_wrong = [
        str(thread_id)
        for thread_id in plan["projectlessThreadIds"]
        if by_thread.get(str(thread_id)) is not None
        and by_thread[str(thread_id)].get("section") is not None
        and not (
            isinstance(by_thread[str(thread_id)].get("section"), dict)
            and by_thread[str(thread_id)]["section"].get("name") == "Pinned"
        )
    ]
    ordered_live_names = [
        str(section["name"])
        for section in sections
        if str(section["name"]) in planned_name_set
    ]
    order_ok = ordered_live_names == planned_names
    result = {
        "verified": not missing_sections
        and not wrong
        and not projectless_wrong,
        "sections": len(planned_names),
        "assignedThreads": sum(
            1
            for section in plan["sections"]
            for thread_id in section["threadIds"]
            if str(thread_id) in by_thread
        ),
        "projectlessThreads": sum(
            1
            for thread_id in plan["projectlessThreadIds"]
            if str(thread_id) in by_thread
        ),
        "missingSections": missing_sections,
        "wrongSectionThreads": wrong,
        "projectlessWrong": projectless_wrong,
        "sectionOrderAlphabetical": order_ok,
        "sectionOrderGuaranteed": False,
    }
    if not result["verified"]:
        raise RuntimeError("section sync verification failed: " + json.dumps(result))
    return result


def apply_plan_data(
    plan: dict[str, object], server: support.AppServer
) -> dict[str, object]:
    _, existing = validate_fresh(plan, server)
    by_name = {str(section["name"]): section for section in existing}
    previous_by_thread = {
        str(item["threadId"]): item for item in plan["previousServerState"]["threads"]
    }
    created: list[dict[str, str]] = []
    moved: list[str] = []
    try:
        for planned in plan["sections"]:
            name = str(planned["name"])
            section = by_name.get(name)
            if section is None:
                section = server.call(
                    "threadSection/create", {"name": name, "appearance": None}
                )["section"]
                by_name[name] = section
                created.append({"id": str(section["id"]), "name": name})
        for planned in plan["sections"]:
            section_id = str(by_name[str(planned["name"])]["id"])
            for thread_id in planned["threadIds"]:
                server.call(
                    "thread/section/move",
                    {
                        "threadId": str(thread_id),
                        "sectionId": section_id,
                        "beforeThreadId": None,
                    },
                )
                moved.append(str(thread_id))
        verification = verify_plan(plan, server)
        return {
            "format": "codex-sidebar-section-sync-receipt/v1",
            "applied": True,
            "verified": True,
            "completedAt": int(time.time()),
            "createdSections": created,
            "movedThreads": moved,
            "verification": verification,
            "previousServerState": plan["previousServerState"],
        }
    except Exception:
        for thread_id in reversed(moved):
            previous = previous_by_thread[thread_id]
            server.call(
                "thread/section/move",
                {
                    "threadId": thread_id,
                    "sectionId": previous["sectionId"],
                    "beforeThreadId": None,
                },
            )
        for section in reversed(created):
            server.call("threadSection/delete", {"sectionId": section["id"]})
        raise


def apply_plan(args: argparse.Namespace) -> None:
    plan = support.load_json(args.plan)
    server = support.AppServer(support.discover_codex(args.codex))
    try:
        result = apply_plan_data(plan, server)
    except Exception as error:
        result = {
            "format": "codex-sidebar-section-sync-receipt/v1",
            "applied": False,
            "verified": False,
            "failedAt": int(time.time()),
            "error": f"{type(error).__name__}: {error}",
        }
        support.write_atomic(args.receipt, support.canonical_json(result))
        raise
    finally:
        server.close()
    result["plan"] = str(args.plan)
    support.write_atomic(args.receipt, support.canonical_json(result))
    print(json.dumps(result, indent=2))


def verify(args: argparse.Namespace) -> None:
    plan = support.load_json(args.plan)
    server = support.AppServer(support.discover_codex(args.codex))
    try:
        print(json.dumps(verify_plan(plan, server), indent=2))
    finally:
        server.close()


def rollback(args: argparse.Namespace) -> None:
    receipt = support.load_json(args.receipt)
    if receipt.get("format") != "codex-sidebar-section-sync-receipt/v1":
        raise ValueError("unsupported section sync receipt")
    if not receipt.get("applied") or not receipt.get("verified"):
        raise RuntimeError("receipt does not describe a successful section sync")
    plan = support.load_json(Path(str(receipt["plan"])))
    server = support.AppServer(support.discover_codex(args.codex))
    try:
        active = server.list_threads(False)
        current_sections = list_sections(server)
        by_thread = {str(thread["id"]): thread for thread in active}
        by_name = {str(section["name"]): section for section in current_sections}
        target_ids = {
            str(by_name[str(section["name"])]["id"])
            for section in plan["sections"]
            if str(section["name"]) in by_name
        }
        for thread_id in receipt["movedThreads"]:
            thread = by_thread.get(str(thread_id))
            if thread is None:
                continue
            section = thread.get("section")
            current_id = section.get("id") if isinstance(section, dict) else None
            if current_id not in target_ids:
                raise RuntimeError(
                    f"task {thread_id} changed sections after apply; refusing rollback"
                )
        previous_by_thread = {
            str(item["threadId"]): item
            for item in receipt["previousServerState"]["threads"]
        }
        for thread_id in receipt["movedThreads"]:
            if str(thread_id) not in by_thread:
                continue
            previous = previous_by_thread[str(thread_id)]
            server.call(
                "thread/section/move",
                {
                    "threadId": str(thread_id),
                    "sectionId": previous["sectionId"],
                    "beforeThreadId": None,
                },
            )
        for created in reversed(receipt["createdSections"]):
            live = next(
                (
                    section
                    for section in list_sections(server)
                    if section.get("id") == created["id"]
                    and section.get("name") == created["name"]
                ),
                None,
            )
            if live is None:
                raise RuntimeError(
                    f"created section {created['name']!r} changed; refusing deletion"
                )
            server.call("threadSection/delete", {"sectionId": created["id"]})
        result = {
            "format": "codex-sidebar-section-sync-rollback/v1",
            "rolledBack": True,
            "completedAt": int(time.time()),
            "restoredThreads": len(receipt["movedThreads"]),
            "deletedSections": len(receipt["createdSections"]),
        }
        support.write_atomic(args.output, support.canonical_json(result))
        print(json.dumps(result, indent=2))
    finally:
        server.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-plan")
    build.add_argument("--state", type=Path, default=support.DEFAULT_STATE)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--codex", type=Path)
    build.set_defaults(func=build_plan)
    apply = subparsers.add_parser("apply-plan")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--receipt", type=Path, required=True)
    apply.add_argument("--codex", type=Path)
    apply.set_defaults(func=apply_plan)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--plan", type=Path, required=True)
    verify_parser.add_argument("--codex", type=Path)
    verify_parser.set_defaults(func=verify)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--receipt", type=Path, required=True)
    rollback_parser.add_argument("--output", type=Path, required=True)
    rollback_parser.add_argument("--codex", type=Path)
    rollback_parser.set_defaults(func=rollback)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.func(arguments)
