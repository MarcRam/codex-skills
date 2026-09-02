# Codex Sidebar Contracts

## Last verified shape

The workflow was verified on macOS against Codex Desktop's bundled App Server
and these local presentation fields in `~/.codex/.codex-global-state.json`:

- `local-projects`: project ID to project metadata, including `name` and
  `rootPaths`
- `project-order`: ordered project IDs
- `thread-project-assignments`: thread ID to `{projectKind, projectId}`
- `projectless-thread-ids`: explicitly unassigned thread IDs
- `sidebar-project-thread-orders`: project ID to explicit `threadIds`

The shared catalogue is available from App Server `thread/list`. Desktop
project metadata is local and is not itself a cross-client grouping contract.

Current App Server v2 also exposes independently persisted, user-visible thread
sections:

- `threadSection/list`
- `threadSection/create`
- `threadSection/update`
- `threadSection/delete`
- `thread/section/move`

`Thread.section` identifies the selected section and `Thread.sectionEnteredAt`
records entry time. The generated protocol describes section appearance as
synchronized across clients. Use these server-owned sections, not Desktop's
JSON project assignments, for the Remote-visible mirror.

The section contract does not expose a cross-client section-order field.
Verify synchronized names and memberships only. Sort Desktop projects and
custom sections through Desktop's supported sidebar operations, and treat the
iPhone's final ordering as a separate visual boundary rather than claiming the
App Server controls it.

## Inputs used by the script

1. Installed App Server executable, discovered from `CODEX_EXECUTABLE`, the
   macOS ChatGPT/Codex application bundle, or `PATH`.
2. Desktop global state, discovered from `CODEX_HOME` or `~/.codex`.
3. Active thread records containing at least `id`, `cwd`, and a display field
   such as `name` or `preview`.
4. A user-reviewed private spec containing new/updated projects and explicit
   assignment rules.
5. macOS process and launchd behavior for the restart-gated apply step.
6. App Server section records and each active task's current `section` value.

Pinned tasks are an overlay and cannot simultaneously occupy an ordinary
section. Preserve them in `Pinned` and exclude them from ordinary membership
checks. A task that has been reviewed and placed directly into an existing
project-named section may be adopted as a section-only assignment when local
Desktop project metadata has not yet caught up. Archived tasks are outside the
active verification set. Build synchronized section order from the live task
labels and local assignment map; do not require Desktop's optional per-project
thread-order cache to be current.

## Release-change audit

Before mutation after a Codex update:

1. Run `inventory` and confirm required state keys and value types.
2. Generate or inspect the installed App Server v2 schema. Confirm
   `threadSection/list`, `threadSection/create`, `threadSection/delete`,
   `thread/section/move`, and `Thread.section` still exist. Confirm that no
   server section-order field has appeared before changing the ordering
   boundary.
3. Query `thread/list` and `threadSection/list` read-only; confirm pagination
   fields and active/archived filters.
4. Inspect one existing local assignment and one project-order entry.
5. Confirm the Desktop main-process path used by the running guard.
6. Build synthetic local and section plans; run both bundled test modules.
7. If any contract differs, update the scripts, this reference, fixtures, and
   tests together. Do not weaken stale-state, backup, or verification gates to
   force compatibility.

## Known boundary

Programmatic checks can prove catalogue membership, Desktop metadata, and
server-owned section membership. They cannot prove what the iPhone Remote
client ultimately renders. Require visual user confirmation for that boundary.
