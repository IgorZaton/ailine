# `ailine track` contract

`ailine track -- <argv>` is the primary way to add reproducibility tracking to
an existing ML project without rewriting your training code. This doc
defines the **contract**: what AIline guarantees, what it expects from your
project, and how `.ailine.yml` configures the boundary.

## TL;DR

```bash
pip install ailine-lineage
cd /path/to/your/repo
ailine init-workspace          # writes a default .ailine.yml (review it)
ailine doctor                  # green-light all checks
ailine track -- python train.py --epochs 5
```

The first token after `--` is the **program** to execute (looked up on `PATH`
or as a path). It is not a shell: `ailine track -- repo/train.py` fails
because `repo/train.py` is not an executable binary. Use
`ailine track -- python repo/train.py` (or `chmod +x` and a shebang).

`track`:

1. Walks parent directories from cwd to find a Git work-tree (`.git`).
2. Loads and validates `.ailine.yml` at the work-tree root.
3. If your tree is dirty (uncommitted changes / untracked files), creates a
   content-addressed snapshot (manifest + diff + metadata + per-file objects)
   under the storage directory configured by `snapshot.storage_dir` in
   `.ailine.yml` (default: `<repo>/.ailine/snapshots`; overridable with the
   `AILINE_STORAGE_DIR` environment variable).
4. Captures DVC linkage, environment fingerprint, the exact argv, and your
   Git state into the SQLite tree.
5. Runs `python train.py --epochs 5` (or whatever you put after `--`) as a
   subprocess from the work-tree, **propagating the exit code**.

The lineage row is **inserted with `status = in_progress` before the
subprocess starts**, then finalized to `done` (exit 0) or `failed` (non-zero
exit, or any AIline-side error after the row was published). This means a
freshly-launched run is visible in `ailine status` and the web UI from
second zero, and a crashed run never gets stuck displaying as in-progress
forever — see [Run status lifecycle](#run-status-lifecycle).

## What AIline does NOT do

- **No magic auto-detection** of frameworks. Your script keeps full control.
- **No silent `dvc add`**. AIline only records DVC linkage from existing
  `.dvc` files / `dvc.yaml`. You own dataset versioning.
- **No outer MLflow run** by default. If your training script calls
  `mlflow.start_run`, that run is the run; AIline just records that it
  happened. See `track.mlflow.mode` below.
- **No edits to your code or working tree.** The snapshot is read-only.

## `.ailine.yml` schema (v1)

The file lives at the Git work-tree root. All sections are optional; defaults
listed below are used when a key is omitted.

```yaml
project:
  version: 1                # required if present; only v1 is supported today
  mode: track               # track | demo

track:
  repo_root: auto           # 'auto' walks parents for .git, or absolute path
  mlflow:
    mode: inherit           # inherit | wrap | none
    set_env: false          # if true, ailine sets MLFLOW_TRACKING_URI before child
    prelink: true           # inherit-only: pre-create MLflow run, export MLFLOW_RUN_ID
  dvc:
    verify: "off"           # off | warn | strict
    verify_commands: []     # e.g. [["dvc", "status", "--quiet"]]

snapshot:
  storage_dir: .ailine/snapshots   # relative paths resolved against the repo root;
                                   # overridden by AILINE_STORAGE_DIR
  # Snapshot ignore patterns live in `.ailineignore` (gitignore syntax) at
  # the repo root. `exclude_globs` here is rejected with a migration error.
  large_file_mb: 50         # files at or above this size go through large-file policy
  large_file_mode: prompt   # prompt | skip | include
  dvc_pointer_patterns:
    - "*.dvc"

dvc:
  remote_name:              # null = first available remote, or set explicitly
  require_hash_fields: true # if true, missing md5/etc downgrades linkage to 'partial'
  ignore_paths: []          # globs of DVC outputs to skip during linkage discovery

environment:
  enabled: true
  packages:                 # versions captured into the env fingerprint
    - mlflow
    - dvc

run_capture:
  enabled: true             # persist full argv + cwd + timestamp per run

cleanup:
  remove:
    with_mlflow: false      # default for `ailine remove`; CLI flag overrides
```

### `track.mlflow.mode`

| Mode      | What ailine does                                                                                          | When to use                                                                |
|-----------|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| `inherit` | Nothing. Your script's own `mlflow.start_run` is the run.                                                  | Default. Most modern training scripts already use MLflow.                  |
| `wrap`    | Opens an outer `mlflow.start_run(run_name=...)` around your child.                                         | Quick wins for scripts that do not log to MLflow themselves.               |
| `none`    | No MLflow side effects whatsoever (does not even resolve `MLFLOW_TRACKING_URI`).                           | When you do not use MLflow at all and want zero coupling.                  |

If `track.mlflow.set_env: true`, AIline sets `MLFLOW_TRACKING_URI` in the
child environment before spawning. Useful when you do not want to bake the
URI into your training code.

### `track.mlflow.prelink` (inherit mode)

When `mode: inherit` and `prelink: true` (the default), AIline pre-creates an
MLflow run via the MLflow client API and exports `MLFLOW_RUN_ID` into the
child environment before spawning. A plain `mlflow.start_run()` in your
training script then resumes that run, so the AIline lineage row carries the
real MLflow run id (and a clickable UI link) from the moment it is published
- without requiring any `import ailine` in user code.

The experiment for the pre-created run is resolved from
`MLFLOW_EXPERIMENT_ID` then `MLFLOW_EXPERIMENT_NAME` (creating the named
experiment if missing), falling back to MLflow's `Default` experiment
(id `0`).

Set `prelink: false` if your script explicitly opens runs with
`mlflow.start_run(run_id=...)`, manages multiple top-level runs in one
process, or otherwise needs MLflow to mint a fresh run id at script start.
The setting is ignored for `mode: wrap` and `mode: none`. See the
[Limitations](../README.md#limitations) section for full troubleshooting.

### `track.dvc.verify`

`verify_commands` is a list of argv lists. Before running your child, AIline
runs each one from the repo root.

| `verify` | Behaviour on non-zero exit                          |
|----------|------------------------------------------------------|
| `off`    | Commands are NOT run.                                |
| `warn`   | Logs a warning; child still runs.                    |
| `strict` | Aborts with a `SessionError` before child starts.    |

Example: enforce `dvc status` is clean before training.

```yaml
track:
  dvc:
    verify: strict
    verify_commands:
      - ["dvc", "status", "--quiet"]
```

### `cleanup.remove.with_mlflow`

Default for `ailine remove <id>`. When `true`, the linked MLflow run (if
any) is also deleted via `MlflowClient.delete_run` after the local cleanup
finishes. When `false` (the default), AIline only touches its own data.

Resolution order, highest to lowest:

1. Explicit CLI value: `ailine remove <id> --with-mlflow true|false`.
2. `cleanup.remove.with_mlflow` from `.ailine.yml`.
3. Built-in default `false`.

`ailine purge` is intentionally not configurable through `.ailine.yml`: it
always asks `Confirm? [y/N]` interactively before deleting (use
`--dry-run` to preview without prompting). Both commands respect
`--config PATH` for ad-hoc test environments.

## `.ailineignore`

Snapshot ignore patterns live in a top-level `.ailineignore` file with full
[gitignore](https://git-scm.com/docs/gitignore) syntax (parsed via
`pathspec`). The same `PathSpec` is consulted by:

- **snapshot scan** — ignored paths are not stored in the snapshot manifest
  or object store;
- **`ailine restore`** — ignored paths are preserved on disk during the
  strict-sync (a dirty `.cursor/foo.json` will not block restore, and is
  not deleted to make the worktree match the snapshot).

A built-in default ignore set is **always active** even when
`.ailineignore` is missing. It covers the common AIline-internal,
Python-build, virtualenv, lint/test cache, IDE/AI-assistant scratch,
ML-experiment (`mlruns/`, `wandb/`, `lightning_logs/`, ...), and DVC
internal directories. `ailine init-workspace` and `ailine init-demo`
seed a fully populated `.ailineignore` so users see (and can edit) the
defaults; with `--force` they overwrite any existing file.

Use `!pattern` to negate a default — for example to keep `dist/keep.txt`
while still ignoring the rest of `dist/`:

```gitignore
!dist/keep.txt
```

`snapshot.exclude_globs` in `.ailine.yml` is no longer supported and is
rejected with a migration error.

## Run status lifecycle

Every recorded run carries an explicit status flag so you can tell live runs
apart from finished ones at a glance in `ailine status`, the web UI, and any
custom tooling that reads the SQLite tree:

| Status        | When set                                                                                         |
|---------------|--------------------------------------------------------------------------------------------------|
| `in_progress` | Row is published right after AIline resolves the commit/snapshot id and (in `wrap` mode) opens the MLflow run, before the child subprocess starts. |
| `done`        | Child subprocess returned exit code `0`. `finished_at` and `exit_code` are persisted.            |
| `failed`      | Child subprocess returned non-zero, or AIline itself errored *after* publishing the row.         |

In `wrap` mode the MLflow run id (and a clickable UI link) are printed
alongside the `in_progress` announcement so you can open the live run before
the first epoch finishes. In `inherit` mode AIline still associates the
in-script MLflow run with the lineage row (best-effort) once the child
exits.

Existing rows recorded by older AIline versions (where `status` is `NULL`)
are interpreted as `done` for backward-compatible display.

## AIline state directory (`.ailine/`)

AIline's own auto-generated artifacts live under `.ailine/` next to the
snapshots dir, so the project root stays clean:

| Artifact            | Path                            |
|---------------------|---------------------------------|
| Lineage SQLite DB   | `.ailine/tree.db`               |
| Log file            | `.ailine/ailine.log`            |
| Demo bookkeeping    | `.ailine/demo-config.txt`       |
| Snapshots           | `.ailine/snapshots/`            |
| Object store        | `.ailine/objects/`              |

User-controlled paths (`mlruns/`, `repo/`, `.ailine.yml`, `.ailineignore`)
are **not** relocated. On the first invocation in an older checkout AIline
moves any legacy root-level artifacts (`ailine_tree.db`, `ailine.log`,
`ailine_config.txt`) into `.ailine/`; if both legacy and new copies exist
the legacy file is left in place and a warning is logged. Each path can
still be overridden with the matching `AILINE_*` environment variable
(`AILINE_DB_PATH`, `AILINE_LOG_PATH`, `AILINE_CONFIG_PATH`,
`AILINE_STORAGE_DIR`, `AILINE_STATE_DIR`).

## Reproducibility per run

For every recorded run AIline persists:

- `commit` (git sha) **or** `snapshot` (content-addressed snapshot id),
- the diff against parent commit (snapshots only),
- the manifest of every included/excluded/pointer file,
- DVC linkage (paths, hashes, cache/remote presence),
- environment fingerprint (Python, platform, `poetry.lock` sha, package versions),
- the exact argv + cwd of the child process,
- the MLflow run id (`wrap`: outer run; `inherit`: best-effort match to a run
  started by your script during the child process, when discoverable).

See [repro-contract.md](repro-contract.md) for the snapshot guarantees.

## Replay expectations (Python ML, best-effort)

AIline is aimed at **Python-centric ML** projects. For each run it stores what is
needed to argue the **next execution can be brought as close as is reasonable**
to the recorded one—not a hardware certification or a line-by-line audit of
every third-party library.

**What we maximize (today):**

- **Pre-run workspace state** under the Git work-tree: if the tree is “dirty”
  (including untracked files), a snapshot captures the included paths per
  policy (exclusions, large-file / DVC-pointer rules) **before** your `argv`
  starts. That is the strongest guarantee: “this is the code and local files
  the process could see at launch.”
- **How the run was launched:** exact `argv` and `cwd`.
- **Coarse environment:** Python version, platform, lockfile fingerprint, and
  configured package versions—not a bitwise copy of `site-packages`.
- **Data lineage where DVC is used:** linkage from existing `.dvc` /
  `dvc.yaml`—you still own remotes and pulls.
- **MLflow association** when available, for cross-checking metrics and
  artifacts in the tracking store.

**What we explicitly do not guarantee:**

- **Bit-identical reruns** of stochastic training (same seed ≠ same weights
  across GPUs, drivers, cuDNN settings, workers, etc.).
- **Files created only after** the snapshot step (e.g. checkpoints written mid-
  run, downloads during the run). Those are out of the pre-run archive unless
  you add a separate capture strategy later (e.g. DVC outputs, MLflow artifacts,
  post-run scan).
- **Reads outside the work-tree** or paths excluded by policy.
- **Hardware equivalence** or per-file verification of vendored / compiled
  dependencies beyond version metadata.

If you need stronger parity for **outputs**, treat them as first-class artifacts
(DVC `outs`, MLflow logged files, or a future post-run snapshot pass)—not as
something implied by the pre-run snapshot alone.

## Migrating from `ailine run`

`ailine run` (the demo flow) keeps working but is now a thin wrapper around
the same session as `ailine track`. The differences:

| Concern              | `ailine run` (demo)                  | `ailine track`                                  |
|----------------------|--------------------------------------|--------------------------------------------------|
| Repo location        | hard-coded `./repo`                  | resolved from cwd or `track.repo_root`           |
| Command              | hard-coded `python <script>`         | arbitrary argv after `--`                        |
| MLflow               | always `wrap`                         | per `track.mlflow.mode` (default `inherit`)      |
| `dvc add`            | opt-in via `--dvc-add` (off by default)| never (you own DVC)                              |
| Required pre-step    | `ailine init-demo <repo_url>`        | `ailine init-workspace` (writes `.ailine.yml`)   |

For your own projects, prefer `ailine track --`.
