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

The shared catalogue is available from App Server `thread/list`. Current App
Server v2 exposes synchronized project records and membership through:

- `project/list`, `project/read`, `project/create`, `project/import`,
  `project/update`, `project/move`, and `project/delete`
- `thread/metadata/update` with `projectId`
- `Thread.projectId` and `thread/project/updated`

Desktop's legacy local assignment map is migration input, not the final
cross-client grouping contract. The local migration status may explicitly show
`projectsMigrated: true` while `threadAssignmentsMigrated: false`; in that state
Remote can see project names without the expected member tasks.

App Server also exposes independently persisted, user-visible thread
sections:

- `threadSection/list`
- `threadSection/create`
- `threadSection/update`
- `threadSection/delete`
- `thread/section/move`

`Thread.section` identifies the selected section and `Thread.sectionEnteredAt`
records entry time. Sections are not interchangeable with projects. They may be
mirrored as an optional Desktop organization aid, but Remote project
synchronization must use App Server projects and `Thread.projectId`.

The section contract does not expose a cross-client section-order field.
Verify synchronized names and memberships only. Sort Desktop projects and
custom sections through Desktop's supported sidebar operations, and treat the
iPhone's final ordering as a separate visual boundary rather than claiming the
App Server controls it.

The verified iPhone client uses ordinary case-insensitive lexical collation,
without numeric normalization. For example, `00 Rust Dev` sorts before
`000 Components`. Use the same key for Desktop, App Server project positions,
and per-project task ordering so both clients agree.

## Inputs used by the script

1. Installed App Server executable, discovered from `CODEX_EXECUTABLE`, the
   macOS ChatGPT/Codex application bundle, or `PATH`.
2. Desktop global state, discovered from `CODEX_HOME` or `~/.codex`.
3. Active thread records containing at least `id`, `cwd`, and a display field
   such as `name` or `preview`.
4. A user-reviewed private spec containing new/updated projects and explicit
   assignment rules.
5. macOS process and launchd behavior for the restart-gated apply step.
6. App Server project records and each active task's current `projectId`.

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
   `project/list`, `project/move`, `thread/metadata/update`, and
   `Thread.projectId` still exist.
3. Query `thread/list` and `project/list` read-only; confirm pagination
   fields and active/archived filters.
4. Inspect one existing local assignment and one project-order entry.
5. Confirm the Desktop main-process path used by the running guard.
6. Build synthetic local and project plans; run the bundled test modules.
7. If any contract differs, update the scripts, this reference, fixtures, and
   tests together. Do not weaken stale-state, backup, or verification gates to
   force compatibility.

## Known boundary

Programmatic checks can prove catalogue membership, Desktop metadata, and
server-owned section membership. They cannot prove what the iPhone Remote
client ultimately renders. Require visual user confirmation for that boundary.
