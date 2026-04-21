# Workspace & Portable Bundle Schema (Pass 5 — frozen)

This document is the single, canonical description of the on-disk
schema for named workspaces (session manifests) and portable bundles
exported from OMLX. It supersedes ad-hoc schema notes scattered in
earlier docs. Related:

* [`runtime_ownership_table.md`](./runtime_ownership_table.md) — what
  runtime code OMLX owns versus delegates.
* [`final_branch_identity.md`](./final_branch_identity.md) — clinical
  statement of what this branch is and is not.

---

## 1. Version constants

All constants live in `omlx/cache/session_archive.py` and
`omlx/cache/session_archive_portable.py`. They are the single source
of truth; docs mirror them.

| Constant | Value | Source |
|---|---|---|
| `MANIFEST_VERSION` | `"2"` | `session_archive.py` |
| `LEGACY_MANIFEST_VERSION` | `"1"` | `session_archive.py` |
| `SUPPORTED_MANIFEST_VERSIONS` | `("1", "2")` | `session_archive.py` |
| `BUNDLE_VERSION` | `"1"` | `session_archive_portable.py` |
| `_CACHE_LAYOUT` | `"paged-ssd-safetensors/v1"` | `session_archive_portable.py` |

Drift in any of these is a compatibility event and is pinned by
`tests/test_session_archive_schema_contract.py`.

---

## 2. Workspace manifest (v2)

One manifest file per `(model_name, session_id)` at
`<archive_root>/<model_slug>/<session_slug>/manifest.json`.

### 2.1 Required top-level fields

| Field | Type | Notes |
|---|---|---|
| `version` | str | Must be in `SUPPORTED_MANIFEST_VERSIONS`; committed manifests always write the current `MANIFEST_VERSION`. |
| `model_name` | str | Logical model identifier; matched against the caller on every `load()`. |
| `session_id` | str | Logical session (workspace) name. |
| `head_turn_id` | str | `t-N`-shaped id of the head turn. Must resolve to an entry in `turns`. |
| `turns` | list[object] | Ordered; each entry describes one committed turn. See §2.3. |
| `model_compat` | object | Compatibility family. See §2.2. |

### 2.2 `model_compat` object

```
{
  "model_name": str,         # mirrors top-level model_name
  "block_size": int | null,  # declared block size (paged-ssd)
  "schema": str              # always equals MANIFEST_VERSION at commit time
}
```

Two manifests are in the same compatibility family iff their
`model_compat` tuples are equal element-wise. Imports refuse bundles
whose `model_compat` disagrees with the caller.

### 2.3 `turns` entries

Each turn is a JSON object containing at minimum:

| Field | Type | Notes |
|---|---|---|
| `turn_id` | str | Shape `t-N` where N is the 1-based turn index. |
| `block_hashes` | list[str] | Hex sha256 block hashes for the cumulative KV-prefix at this turn. |
| `committed_at` | float | Unix epoch seconds. |
| `note` | str \| null | Optional operator-supplied note. |
| `branch_reason` | str \| null | Optional reason string when this turn opens a fork. |

### 2.4 Optional lineage fields

If the session is a fork, the manifest carries a `lineage` object
recording `parent_session_id`, `parent_turn_id`, and a rebased turn
map. Absence means the session is a root.

### 2.5 Validation errors

`SessionArchiveStore.load()` raises `SessionArchiveError` with
diagnostic-shaped messages. The strings below are stable — operator
tooling greps them:

* `"malformed manifest: ..."` — I/O failure, non-JSON content, missing
  required fields, or a `head_turn_id` that does not resolve.
* `"compatibility mismatch: ..."` — `version` not in
  `SUPPORTED_MANIFEST_VERSIONS`, or `model_name` disagrees with the
  caller.
* `"malformed manifest: session_id mismatch ..."` — stored `session_id`
  disagrees with the caller (slug collision / copied directory).

### 2.6 v1 → v2 behaviour

v1 manifests store `block_hashes` at the top level instead of a
`turns[].block_hashes` list. They are read-compatible: `load()` maps
them into a single synthetic turn in memory. The next successful
`commit()` persists the session as v2. v1 export is not supported —
exports always write v2.

### 2.7 Optional operational fields (additive, non-breaking)

Pass 6 adds two optional top-level fields. Both are strictly additive;
their absence is equivalent to the default, and both `MANIFEST_VERSION`
and `SUPPORTED_MANIFEST_VERSIONS` remain unchanged.

| Field          | Type            | Default | Semantics                                                                                     |
|----------------|-----------------|---------|-----------------------------------------------------------------------------------------------|
| `pinned`       | `bool`          | `false` | Operator-set retention guard. Pinned workspaces are protected from pruning unless the operator explicitly passes `--include-pinned --confirm`. |
| `last_used_at` | `float \| null` | `null`  | Epoch seconds of the most recent *useful* access (successful `replay_check`, export, or import). Writes do **not** update `updated_at` so "useful" is distinguishable from "mutated". |

The v2 validator silently preserves unknown top-level keys; readers
that do not understand `pinned` or `last_used_at` ignore them without
error. Bundle pinning is recorded via a sidecar marker file
`<bundle_path>.pinned` instead of inside the tarball, so pinning a
bundle never changes its bytes or content hash.

Canonical policy reference: [pruning_policy.md](pruning_policy.md).

---

## 3. Portable bundle (v1)

Exports produce a tar archive of the shape:

```
bundle.omlx-session.tar
├── bundle.json        # envelope
├── manifest.json      # v2 workspace manifest (embedded copy)
└── blocks/            # sha256-named *.safetensors block payloads
```

### 3.1 Envelope (`bundle.json`) required keys

The full tuple is `_ENVELOPE_KEYS` in `session_archive_portable.py`:

1. `bundle_version` — string, must equal `BUNDLE_VERSION`.
2. `created_at` — float, unix epoch seconds.
3. `model_name` — string.
4. `session_id` — string, must equal the embedded manifest's `session_id`.
5. `head_turn_id` — string, must equal the embedded manifest's `head_turn_id`.
6. `block_count` — int.
7. `block_sha256` — mapping `block_hash_hex → sha256_of_file` for each exported block.
8. `source_cache_layout` — string, must equal `_CACHE_LAYOUT`.
9. `source_label` — operator-supplied short label.
10. `source_description` — operator-supplied long description.
11. `task_tag` — free-form operator tag.
12. `model_compat` — copy of the manifest's compatibility object.
13. `platform` — `{system, machine, python_version}` map captured at export time.
14. `exporter_version` — equals `MANIFEST_VERSION` (tracks the schema the exporter writes).
15. `git_commit` — best-effort git HEAD of the exporting repo, or `null`.

### 3.2 Bundle validation

`inspect_bundle()` (and `import_session()` by extension) raise
`BundleError` with these stable message prefixes:

* `"bundle not found: ..."` — path missing.
* `"bundle unreadable: ..."` — tar open failed.
* `"bundle missing required files ..."` — missing `bundle.json` or `manifest.json`.
* `"bundle envelope missing required key ..."` — any one of
  `_ENVELOPE_KEYS` absent from `bundle.json`.
* `"bundle version mismatch ..."` — `bundle_version` not equal to `BUNDLE_VERSION`.
* `"bundled manifest must be schema v2 ..."` — embedded manifest's `version` != `MANIFEST_VERSION`.
* `"bundle envelope/manifest session_id mismatch ..."` — envelope/manifest disagree.
* `"bundle envelope/manifest head_turn_id mismatch ..."` — envelope/manifest disagree.

### 3.3 Import policy

`import_session()` verifies every block's sha256 against the envelope
before writing it to the destination SSD layout. Conflict handling
(`--fail-if-exists`, `--rekey`, `--overwrite`) is the admin CLI's
responsibility; the library default is `fail`.

---

## 4. Compatibility family

Two artifacts are in the same family iff their `model_compat` tuples
`(model_name, block_size, schema)` match element-wise. Imports refuse
bundles that do not match the caller's expected family. There is no
cross-family migration path — this is intentional.

---

## 5. Integrity-grade vocabulary

Operator tooling (retention, replay-check, diff, admin CLI) emits
exactly these seven grade strings (`session_archive.py`):

| Constant | Literal | Meaning |
|---|---|---|
| `INTEGRITY_HEALTHY` | `"healthy"` | Manifest loads, all referenced blocks present, compat matches. |
| `INTEGRITY_STALE` | `"stale"` | Healthy but older than the retention window. |
| `INTEGRITY_INVALID_MANIFEST` | `"invalid_manifest"` | Manifest JSON malformed or fails structural checks. |
| `INTEGRITY_MISSING_BLOCKS` | `"missing_blocks"` | One or more referenced blocks absent from SSD cache. |
| `INTEGRITY_INCOMPATIBLE_MODEL` | `"incompatible_model"` | Manifest loads but compatibility family mismatch. |
| `INTEGRITY_UNREADABLE` | `"unreadable"` | Manifest file cannot be opened/read. |
| `INTEGRITY_PARTIALLY_EXPORTABLE` | `"partially_exportable"` | Export succeeded under `allow_missing_blocks`; bundle is explicitly not round-tripable. |

The set is closed. Adding a new grade is a breaking change and is
guarded by
`tests/test_session_archive_schema_contract.py::test_integrity_grade_vocabulary_is_fixed`.

---

## 6. Migration policy

### 6.1 Non-breaking changes

* Adding a new optional top-level field to the manifest.
* Adding a new optional envelope key (must also be added to
  `_ENVELOPE_KEYS` if required; see §6.2).
* Adding a new integrity grade used only internally.

### 6.2 Breaking changes (bump `MANIFEST_VERSION` / `BUNDLE_VERSION`)

* Dropping a required field or changing its semantics.
* Adding a new required envelope key.
* Removing an entry from `SUPPORTED_MANIFEST_VERSIONS`.
* Changing the literal value of any integrity-grade constant or
  removing a grade from the public set.

Any of the above requires the version constant to advance **and** the
schema-contract test suite to be updated in the same commit.

---

## 7. Proof tests

The following tests directly assert the invariants stated here:

* `tests/test_session_archive_schema_contract.py` — version constants,
  unknown-future-version rejection, bundle-version-mismatch rejection,
  missing-envelope-key rejection (parametrized over all keys),
  `model_compat.schema` locking, integrity vocabulary closure.
* `tests/test_session_archive_hardening.py` — head-turn rejection,
  fork-at-head, diff stability, replay-check grades.
* `tests/test_session_archive_failure_modes.py` — malformed manifests,
  mismatched session ids, bundle-level failures.
* `tests/test_session_archive_lineage.py` — export/import round-trip,
  `allow_missing_blocks` grade, wrong-model import rejection.
* `tests/test_workspace_lineage.py` — end-to-end workspace surfaces.

---

## 8. Non-goals

* No support for multiple active bundle versions at once; import
  refuses anything that is not the current `BUNDLE_VERSION`.
* No schema evolution path for v0 or any pre-v1 format — they never
  shipped publicly and are not supported.
* No implicit cross-model or cross-block-size migration.
* No public stability claim beyond this document; OMLX remains an
  internal operator tool.
