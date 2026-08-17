from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
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


reconcile = load_module("reconcile_for_sections", SCRIPTS / "reconcile.py")
sync_sections = load_module("sync_sections", SCRIPTS / "sync_sections.py")


class FakeServer:
    def __init__(self, threads: list[dict[str, object]]) -> None:
        self.threads = copy.deepcopy(threads)
        self.sections: list[dict[str, object]] = [
            {"id": "pinned", "name": "Pinned", "appearance": None}
        ]

    def list_threads(self, archived: bool) -> list[dict[str, object]]:
        assert not archived
        return copy.deepcopy(self.threads)

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "threadSection/list":
            return {"data": copy.deepcopy(self.sections), "nextCursor": None}
        if method == "threadSection/create":
            section = {
                "id": f"section-{len(self.sections)}",
                "name": params["name"],
                "appearance": params.get("appearance"),
            }
            self.sections.append(section)
            return {"section": copy.deepcopy(section)}
        if method == "thread/section/move":
            section_id = params["sectionId"]
            section = next(
                (item for item in self.sections if item["id"] == section_id), None
            )
            thread = next(
                item for item in self.threads if item["id"] == params["threadId"]
            )
            thread["section"] = copy.deepcopy(section)
            return {}
        if method == "threadSection/delete":
            self.sections = [
                item for item in self.sections if item["id"] != params["sectionId"]
            ]
            return {}
        raise AssertionError(method)


class SyncSectionTests(unittest.TestCase):
    def load_fixture(self, name: str) -> object:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def build_inputs(
        self, root: Path
    ) -> tuple[Path, dict[str, object], list[dict[str, object]], FakeServer]:
        state = self.load_fixture("state.json")
        threads = self.load_fixture("threads.json")
        spec = self.load_fixture("spec.json")
        state_path = root / "state.json"
        state_bytes = reconcile.canonical_json(state)
        local_plan = reconcile.make_plan(
            state_bytes, state, spec, threads, state_path, now_ms=123
        )
        reconciled = local_plan["proposedState"]
        state_path.write_bytes(reconcile.canonical_json(reconciled))
        server = FakeServer(threads)
        return state_path, reconciled, threads, server

    def test_plan_maps_reconciled_projects_to_shared_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path, state, threads, server = self.build_inputs(Path(directory))
            plan = sync_sections.make_section_plan(
                state, threads, server.sections, state_path
            )
            self.assertEqual(
                [section["name"] for section in plan["sections"]],
                ["Alpha", "Existing", "Research"],
            )
            self.assertEqual(plan["assignedThreads"], 4)
            self.assertEqual(plan["projectlessThreads"], 1)

    def test_apply_creates_and_verifies_shared_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path, state, threads, server = self.build_inputs(Path(directory))
            plan = sync_sections.make_section_plan(
                state, threads, server.sections, state_path
            )
            result = sync_sections.apply_plan_data(plan, server)
            self.assertTrue(result["verified"])
            self.assertEqual(result["verification"]["sections"], 3)
            self.assertEqual(result["verification"]["assignedThreads"], 4)
            self.assertEqual(
                [section["name"] for section in server.sections],
                ["Pinned", "Alpha", "Existing", "Research"],
            )


if __name__ == "__main__":
    unittest.main()
