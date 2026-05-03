# Reproducibility Contract (Stage 1)

This document defines what AIline guarantees for each experiment run.

## What "exact version" means

For every run, AIline stores a code-state record that can be traced to:

- a Git commit (`type=git`) when the repository is clean, or
- a snapshot (`type=snapshot`) when the repository is dirty.

For dirty repositories, AIline records:

- `parent_commit`: the current `HEAD` commit at run time
- a full tracked/untracked file manifest (subject to configured exclusions)
- a git patch (`git diff HEAD`) for tracked changes
- untracked file list and large-file policy decisions

## Snapshot inclusion rules

Snapshots are policy-driven and deterministic:

- Include tracked and untracked files by default.
- Exclude files/paths matching configured exclude globs.
- Exclude `.git/` and runtime/state directories.
- For large files:
  - DVC-managed large files are represented by pointers/metadata, not copied into each snapshot archive.
  - Non-DVC large files use an interactive policy (`include`, `skip`, `abort`).

## Large-file decision memory

When prompted for a large non-DVC file, user decisions are persisted in `.ailine/large-file-policy.json`:

- decision can be remembered per `path + content hash` and path defaults
- remembered decisions are auto-applied in future runs
- metadata records whether a decision came from prompt or memory

## Storage and deduplication guarantees

- Snapshot archive format is `tar.zst`.
- Snapshot ID is computed from manifest content hash.
- Accepted large non-DVC files are stored once in a content-addressed object store under `.ailine/objects/`.
- Snapshot manifests store pointer references to these objects instead of duplicating payloads every run.

## Metadata guarantees per run

Dirty-run snapshot metadata contains:

- `snapshot_id`
- `parent_commit`
- `manifest_path`
- `metadata_path`
- archive checksum/size
- include/exclude counts
- large-file pointers and decision source
- tracked diff path and untracked file list

For **intent, limits, and replay expectations** (what “as close as practical”
means for Python ML), see **Replay expectations** in
[track-contract.md](track-contract.md).
