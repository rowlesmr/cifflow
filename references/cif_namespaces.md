# Namespace handling in a CIF SQLite schema

Version: 0.1  
Date: April 2026  
Context: Multi-block CIF with powder data, implementing the hybrid `_audit_dataset.id` approach

---

## The problem

In single-block CIF, all identifiers (primary keys) are implicitly scoped to that block. In a
multi-block CIF bundle, the same short identifier (e.g. `1`, `sample_a`) may appear in more
than one block. Whether equal values refer to the same thing or different things depends on
which namespace each block belongs to.

---

## The two block classes

The hybrid approach defines two classes of block, distinguished by the presence or absence of
`_audit_dataset.id`.

### Dataset blocks

- Carry one or more `_audit_dataset.id` values.
- Short, human-readable identifiers are safe within a dataset because the dataset id provides
  the disambiguating namespace.
- A block may belong to more than one dataset simultaneously (e.g. a calibration block shared
  across multiple datasets).

### General blocks

- Carry no `_audit_dataset.id`.
- Should use UUIDs for any identifier that may be referenced by other blocks, to avoid collisions
  across an otherwise undefined scope.
- May independent of any specific dataset. The absence of an `_audit_dataset.id` does not necessarily
  mean it doesn't belong.

---

## Namespace resolution rules

These rules are applied when comparing identifier values across blocks.

### Rule 1 — dataset blocks, same `_audit_dataset.id`

If two or more blocks share the same `_audit_dataset.id` value, they are in the same namespace.
Equal PK values across those blocks **must** refer to the same thing.

### Rule 2 — dataset blocks, different `_audit_dataset.id`

If two blocks have different `_audit_dataset.id` values, their namespaces are disjoint.
Equal PK values across those blocks **must** refer to different things, regardless of how
similar the values appear.

### Rule 3 — general blocks with UUIDs

If a block carries no `_audit_dataset.id` and all its identifiers are UUIDs, those identifiers
are globally unique. Equal UUID values across any blocks **must** refer to the same thing.

### Rule 4 — general blocks without UUIDs (assumed coherence)

If a block carries no `_audit_dataset.id` and its identifiers are not UUIDs, no formal
guarantee can be made. However, the act of bundling these blocks together is treated as a weak
assertion of coherence. Equal PK values **may** refer to the same thing. This is recorded as
`id_regime = 'assumed'` and flagged as a warning, not an error.

---

## Confidence ladder

| Situation | `id_regime` | Confidence | Equal PKs mean |
|---|---|---|---|
| All blocks share one `_audit_dataset.id` | `dataset` | Certain | Same thing |
| Blocks have different `_audit_dataset.id` | `dataset` | Certain | Different things |
| General block, UUIDs verified | `uuid` | High | Same thing |
| General block, UUID cross-reference found | `uuid` | High | Those blocks belong together |
| General block, no UUIDs, user bundled | `assumed` | Assumed | Probably the same thing |

---

## Validation checks

These checks should be run at import time and results stored in a `VALIDATION_RESULT` table.

| Check | `check_name` | Severity |
|---|---|---|
| All dataset blocks share the same `_audit_dataset.id` | `dataset_id_consistent` | Warning if mixed |
| General blocks use UUIDs for all PK values | `uuid_regime` | Warning if not |
| General block UUIDs are referenced by dataset blocks | `uuid_reference_check` | Info if not |

---


Note: queries are intentionally non-transitive. If block A belongs to datasets 1 and 2, and
block B belongs to datasets 2 and 3, querying from A returns B (via dataset 2) but does not
imply that datasets 1 and 3 are related.
