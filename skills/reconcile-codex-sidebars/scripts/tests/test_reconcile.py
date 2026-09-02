from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "reconcile.py"
SPEC = importlib.util.spec_from_file_location("reconcile", SCRIPT)
assert SPEC and SPEC.loader
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)
FIXTURES = Path(__file__).with_name("fixtures")


class ReconcileTests(unittest.TestCase):
    def load_fixture(self, name: str) -> object:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def build_synthetic_plan(self, root: Path) -> tuple[Path, dict[str, object]]:
        state = self.load_fixture("state.json")
        threads = self.load_fixture("threads.json")
        spec = self.load_fixture("spec.json")
        state_path = root / "state.json"
        state_bytes = reconcile.canonical_json(state)
        state_path.write_bytes(state_bytes)
        plan = reconcile.make_plan(
            state_bytes,
            state,
            spec,
            threads,
            state_path,
            now_ms=123,
        )
        plan_path = root / "plan.json"
        plan_path.write_bytes(reconcile.canonical_json(plan))
        return plan_path, plan

    def test_plan_partitions_and_sorts_synthetic_threads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, plan = self.build_synthetic_plan(Path(directory))
            threads = self.load_fixture("threads.json")
            result = reconcile.audit_plan_data(plan, threads)
            self.assertEqual(plan["newAssignmentCount"], 2)
            self.assertEqual(plan["changedAssignmentCount"], 1)
            self.assertEqual(plan["projectOrder"], ["Alpha", "Existing", "Research"])
            self.assertEqual(result["assignedActive"], 4)
            self.assertEqual(result["projectlessActive"], 1)
            self.assertEqual(result["missing"], 0)
            self.assertEqual(result["archivedAssignmentsPreserved"], 1)

    def test_apply_creates_backup_and_verifies_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_path, plan = self.build_synthetic_plan(Path(directory))
            with mock.patch.object(reconcile, "desktop_running", return_value=False):
                result = reconcile.apply_plan_data(plan_path)
            self.assertTrue(result["verified"])
            self.assertTrue(Path(result["backup"]).exists())
            self.assertEqual(
                reconcile.verify_state_data(plan_path)["sidebarSha256"],
                plan["proposedSidebarSha256"],
            )

    def test_apply_refuses_running_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_path, _ = self.build_synthetic_plan(Path(directory))
            with (
                mock.patch.object(reconcile, "desktop_running", return_value=True),
                self.assertRaisesRegex(RuntimeError, "Desktop is running"),
            ):
                reconcile.apply_plan_data(plan_path)

    def test_apply_refuses_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path, plan = self.build_synthetic_plan(root)
            state_path = Path(plan["statePath"])
            state = json.loads(state_path.read_text())
            state["project-order"] = list(reversed(state["project-order"]))
            state_path.write_bytes(reconcile.canonical_json(state))
            with (
                mock.patch.object(reconcile, "desktop_running", return_value=False),
                self.assertRaisesRegex(RuntimeError, "sidebar state changed"),
            ):
                reconcile.apply_plan_data(plan_path)

    def test_apply_preserves_unrelated_live_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path, plan = self.build_synthetic_plan(root)
            state_path = Path(plan["statePath"])
            state = json.loads(state_path.read_text())
            state["unrelated-live-value"] = {"changed": True}
            state_path.write_bytes(reconcile.canonical_json(state))
            with mock.patch.object(reconcile, "desktop_running", return_value=False):
                result = reconcile.apply_plan_data(plan_path)
            self.assertTrue(result["verified"])
            written = json.loads(state_path.read_text())
            self.assertEqual(written["unrelated-live-value"], {"changed": True})

    def test_audit_accepts_either_order_for_equal_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, plan = self.build_synthetic_plan(Path(directory))
            threads = self.load_fixture("threads.json")
            existing_id = next(
                project_id
                for project_id, project in plan["proposedState"][
                    "local-projects"
                ].items()
                if project["name"] == "Existing"
            )
            ordered = plan["proposedState"]["sidebar-project-thread-orders"][
                existing_id
            ]["threadIds"]
            self.assertEqual(len(ordered), 2)
            for thread in threads:
                if thread["id"] in ordered:
                    thread["name"] = "Same Title"
            plan["proposedState"]["sidebar-project-thread-orders"][existing_id][
                "threadIds"
            ] = list(reversed(ordered))
            result = reconcile.audit_plan_data(plan, threads)
            self.assertEqual(result["threadOrderErrors"], [])

    def test_watcher_reaudits_then_applies_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path, _ = self.build_synthetic_plan(root)
            receipt = root / "receipt.json"
            with (
                mock.patch.object(
                    reconcile,
                    "desktop_running",
                    side_effect=[True, False, False],
                ),
                mock.patch.object(
                    reconcile,
                    "load_threads",
                    return_value=self.load_fixture("threads.json"),
                ),
                mock.patch.object(reconcile.time, "sleep"),
            ):
                reconcile.watch_apply(
                    argparse.Namespace(
                        plan=plan_path,
                        receipt=receipt,
                        timeout=60,
                        relaunch=False,
                    )
                )
            result = json.loads(receipt.read_text())
            self.assertTrue(result["applied"])
            self.assertTrue(result["verified"])

    def test_arm_restart_creates_non_keepalive_launchd_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path, _ = self.build_synthetic_plan(root)
            codex_home = root / "codex-home"
            runtime_root = codex_home / "sidebar-reconcile-runtime"

            def fake_run(command: list[str], **kwargs: object) -> object:
                if command[:2] == ["launchctl", "print"]:
                    return subprocess_result(0, "state = running\n", "")
                return subprocess_result(0, "", "")

            with (
                mock.patch.object(reconcile, "CODEX_HOME", codex_home),
                mock.patch.object(reconcile, "desktop_running", return_value=True),
                mock.patch.object(
                    reconcile,
                    "load_threads",
                    return_value=self.load_fixture("threads.json"),
                ),
                mock.patch.object(reconcile.platform, "system", return_value="Darwin"),
                mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run),
                mock.patch.object(reconcile.time, "sleep"),
            ):
                reconcile.arm_macos_restart(
                    argparse.Namespace(
                        plan=plan_path,
                        runtime_root=runtime_root,
                        timeout=60,
                    )
                )
            manifests = list(runtime_root.glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text())
            plist = Path(manifest["plist"]).read_bytes()
            decoded = reconcile.plistlib.loads(plist)
            self.assertFalse(decoded["KeepAlive"])
            self.assertTrue(decoded["RunAtLoad"])

    def test_relaunch_waits_for_a_real_desktop_process(self) -> None:
        with (
            mock.patch.object(
                reconcile, "desktop_running", side_effect=[False, False, True]
            ),
            mock.patch.object(reconcile.time, "sleep"),
            mock.patch.object(
                reconcile.subprocess,
                "run",
                return_value=subprocess_result(0, "", ""),
            ) as run,
        ):
            reconcile.relaunch_desktop()
        run.assert_called_once_with(
            ["open", "-a", "ChatGPT"],
            check=False,
            capture_output=True,
            text=True,
        )


def subprocess_result(returncode: int, stdout: str, stderr: str) -> object:
    return type(
        "Completed",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


if __name__ == "__main__":
    unittest.main()
