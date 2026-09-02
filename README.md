# Codex Skills

Public, reusable skills for OpenAI Codex.

## Skills

- `reconcile-codex-sidebars` — safely reconcile Desktop project metadata, then
  mirror it into synchronized App Server sections used across Desktop and
  Remote, with deterministic alphabetical ordering and rollback support.

## Install a skill

Clone the repository, then copy the selected skill into the Codex skills
directory:

```bash
git clone https://github.com/MarcRam/codex-skills.git
cp -R codex-skills/skills/reconcile-codex-sidebars \
  "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installation. Invoke the skill as
`$reconcile-codex-sidebars` or ask Codex to reconcile the Desktop and Remote
sidebars.

## Safety and privacy

The repository contains synthetic fixtures only. Live inventories, thread IDs,
transcripts, plans, receipts, backups, and machine-specific paths are private
runtime artifacts and must never be committed. Read [AGENTS.md](AGENTS.md)
before modifying or publishing a skill.

## Validate

```bash
python3 -m unittest -v \
  skills/reconcile-codex-sidebars/scripts/tests/test_reconcile.py \
  skills/reconcile-codex-sidebars/scripts/tests/test_sync_projects.py \
  skills/reconcile-codex-sidebars/scripts/tests/test_sync_sections.py
python3 /path/to/skill-creator/scripts/quick_validate.py \
  skills/reconcile-codex-sidebars
```

## License

MIT. See [LICENSE](LICENSE).
