from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1]
FIXTURES = Path(__file__).with_name("fixtures")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_projects = load_module("sync_projects", SCRIPTS / "sync_projects.py")


class FakeServer:
    def __init__(
        self,
        threads: list[dict[str, object]],
        projects: list[dict[str, object]],
    ) -> None:
        self.threads = copy.deepcopy(threads)
        self.projects = copy.deepcopy(projects)
        self.fail_on: str | None = None

    def list_threads(self, archived: bool) -> list[dict[str, object]]:
        assert not archived
        return copy.deepcopy(self.threads)

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if self.fail_on is not None and self.fail_on == params.get("threadId"):
            raise RuntimeError("injected failure")
        if method == "project/list":
            return {"data": copy.deepcopy(self.projects), "nextCursor": None}
        if method == "thread/metadata/update":
            thread = next(
                item for item in self.threads if item["id"] == params["threadId"]
            )
            thread["projectId"] = params["projectId"] or None
            return {"thread": copy.deepcopy(thread)}
        if method == "project/move":
            project = next(
                item for item in self.projects if item["id"] == params["projectId"]
            )
            self.projects.remove(project)
            before = params["beforeProjectId"]
            if before is None:
                self.projects.append(project)
            else:
                index = next(
                    i
                    for i, item in enumerate(self.projects)
                    if item["id"] == before
                )
                self.projects.insert(index, project)
            return {}
        raise AssertionError(method)


class SyncProjectTests(unittest.TestCase):
    def inputs(self):
        state = json.loads((FIXTURES / "state.json").read_text())
        threads = json.loads((FIXTURES / "threads.json").read_text())
        for thread in threads:
            thread["projectId"] = None
        projects = [
            {
                "id": "server-research",
                "name": "Research",
                "roots": [{"path": "/work/research"}],
            },
            {
                "id": "server-alpha",
                "name": "Alpha",
                "roots": [{"path": "/work/alpha"}],
            },
            {
                "id": "server-existing",
                "name": "Existing",
                "roots": [{"path": "/work/existing"}],
            },
        ]
        state["app-server-project-id-by-legacy-project-id-by-host"] = {
            "local:test": {
                "alpha": "server-alpha",
                "existing": "server-existing",
            }
        }
        state["thread-project-assignments"]["t-path"] = {
            "projectKind": "local",
            "projectId": "existing",
        }
        state["thread-project-assignments"]["t-explicit"] = {
            "projectKind": "local",
            "projectId": "existing",
        }
        return state, threads, projects

    def test_plan_apply_and_verify_real_project_membership(self) -> None:
        state, threads, projects = self.inputs()
        server = FakeServer(threads, projects)
        raw = json.dumps(state).encode()
        plan = sync_projects.make_plan(
            raw, state, projects, threads, Path("/state")
        )
        result = sync_projects.apply_plan_data(plan, server)
        self.assertTrue(result["verification"]["verified"])
        self.assertEqual(
            [item["name"] for item in server.projects],
            ["Alpha", "Existing", "Research"],
        )
        self.assertEqual(result["applied"], 4)

    def test_partial_failure_rolls_back_membership(self) -> None:
        state, threads, projects = self.inputs()
        server = FakeServer(threads, projects)
        plan = sync_projects.make_plan(
            json.dumps(state).encode(), state, projects, threads, Path("/state")
        )
        server.fail_on = plan["assignmentChanges"][1]["threadId"]
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            sync_projects.apply_plan_data(plan, server)
        self.assertTrue(all(item.get("projectId") is None for item in server.threads))

    def test_plan_refuses_unclassified_active_thread(self) -> None:
        state, threads, projects = self.inputs()
        threads.append(
            {"id": "unknown", "name": "Unknown", "cwd": "/tmp", "projectId": None}
        )
        with self.assertRaisesRegex(ValueError, "not classified"):
            sync_projects.make_plan(
                json.dumps(state).encode(),
                state,
                projects,
                threads,
                Path("/state"),
            )

    def test_plan_adopts_reviewed_project_named_section(self) -> None:
        state, threads, projects = self.inputs()
        state["thread-project-assignments"].pop("t-path")
        target = next(item for item in threads if item["id"] == "t-path")
        target["section"] = {"id": "section-existing", "name": "Existing"}
        plan = sync_projects.make_plan(
            json.dumps(state).encode(), state, projects, threads, Path("/state")
        )
        change = next(
            item for item in plan["assignmentChanges"] if item["threadId"] == "t-path"
        )
        self.assertEqual(change["afterProjectId"], "server-existing")


if __name__ == "__main__":
    unittest.main()
