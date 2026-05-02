# TODO

Goal: deliver full reproducibility functionality with safety-critical protections and an easy-to-use interface.

## P0 - Core Reproducibility (must-have)

- [ ] Add `restore` command
  - `ailine restore <run_id|snapshot_id> [--out <dir>]`
  - Restores exact code state into a separate workspace (never modifies active repo)
  - Uses snapshot archive + manifest + pointer metadata to materialize files

- [ ] Add `reproduce` command
  - `ailine reproduce <run_id> [--out <dir>] [--execute]`
  - Restores code/data/env metadata and optionally reruns original command
  - Prints deterministic reproduction report (matched/mismatched items)

- [x] Persist full run command and config
  - Store exact invocation (`command`, args, script path, config path, seed)
  - Remove hardcoded MLflow params/metrics placeholders
  - Capture user-provided params in structured form

- [x] Capture environment fingerprint
  - Python version, platform info, Poetry lock hash, key package versions
  - Save in run metadata and expose in `status --verbose`

- [x] Strengthen DVC linkage
  - Persist DVC object identity per run (hash/rev/path)
  - Add restore-time check for DVC availability with actionable remediation

## P0 - Safety-Critical Patches

- [ ] Eliminate destructive git operations in web views
  - Replace `reset --hard`, `clean -fd`, direct checkout in live repo
  - Use temporary clone/worktree for commit browsing

- [ ] Safe archive extraction
  - Prevent path traversal (validate tar members before extract)
  - Reject absolute paths and `..` escapes

- [ ] Path and file guardrails
  - Constrain restore output paths
  - Refuse overwrite unless explicit `--force`

- [ ] Crash-safe writes
  - Atomic writes for manifest/metadata/decision-store (temp file + rename)
  - Basic file locking around state DB and decision store

## P1 - Easy-to-Use Interface

- [ ] Add `doctor` command
  - Checks `git`, `dvc`, `mlflow`, python version, config paths, writable dirs
  - Outputs actionable fix commands

- [ ] Improve `status` command
  - Show reproducibility readiness: `Ready`, `Ready with warnings`, `Not reproducible`
  - Include manifest path, diff path, pointer count, env fingerprint status

- [ ] Guided large-file prompts
  - Better prompt text with remember options
  - Always suggest `dvc add <file>` when skipping/aborting

- [ ] Add `init --interactive`
  - Walk through repo URL/path, defaults, storage, exclusions

## P1 - Web UX for Repro Flow

- [ ] Add run detail page
  - Code state, data state, env, command, artifacts in one place

- [ ] Add diff visibility
  - Parent commit + tracked diff + untracked list

- [ ] Add restore/reproduce actions
  - Copy-ready CLI commands from UI for selected run

## P1 - Data Model and Storage Cleanup

- [ ] Normalize DB schema
  - Split into `runs`, `code_states`, `data_states`, `env_states`
  - Keep backward-compatible migration from `tree`

- [ ] Add pointer materialization index
  - Track pointer -> object -> runs references
  - Add garbage collection command for orphan objects

## P2 - Validation and Reliability

- [ ] End-to-end integration tests
  - Dirty repo run -> snapshot -> restore -> rerun -> compare report

- [ ] Safety regression tests
  - Ensure no destructive git calls on user repo
  - Archive extraction traversal tests

- [ ] CLI contract tests
  - `restore`, `reproduce`, `doctor`, `status` output stability

## P2 - Documentation and Onboarding

- [ ] Write full README
  - 5-minute quickstart + reproducibility guarantees + failure modes

- [ ] Add operational playbook
  - Troubleshooting matrix for reproduce/restore failures

- [ ] Add example project
  - Minimal demo of dirty snapshot + reproduce flow
