"""consolidate_component_intensities — aggregate pd_calc_component into pd_calc."""

from __future__ import annotations

import json

import duckdb

from cifflow.dictionary.schema import SchemaSpec
from cifflow.ingestion.ingest import _CONTAINER_PREFIX


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sl(val: str) -> str:
    """Wrap *val* as a SQL string literal (single-quotes escaped)."""
    return "'" + str(val).replace("'", "''") + "'"


def _prefix_sql(json_str: str) -> str:
    """Prepends _CONTAINER_PREFIX to *json_str* at DuckDB runtime.

    Avoids embedding the null-byte prefix in the SQL text itself, which would
    truncate the statement at the SQL parser level.
    """
    return f'chr(0) || {_sl(json_str)}'


def _all_dot(stored: str) -> bool:
    """Return True if every element of the stored container value is '.'."""
    try:
        val = stored[1:] if stored.startswith(_CONTAINER_PREFIX) else stored
        elems = json.loads(val)
        return bool(elems) and all(e == '.' for e in elems)
    except Exception:
        return False


def consolidate_component_intensities(
    connection: duckdb.DuckDBPyConnection,
    schema: SchemaSpec,
    *,
    clear_source: bool = False,
) -> int:
    """Populate ``pd_calc_overall.component_presentation_order`` and ``pd_calc.component_intensities_net`` / ``component_intensities_total`` from ``pd_calc_component`` rows.

    For each ``diffractogram_id``, the distinct ``phase_id`` values are sorted
    alphabetically to define presentation order.  Per-point intensity values
    from ``pd_calc_component`` are assembled into CIF container lists in that
    order, substituting ``'.'`` for any NULL intensity.

    If all assembled values for a column across an entire ``diffractogram_id``
    are ``'.'``-only, that column is left NULL for that diffractogram.  After
    processing all diffractograms, any column that is entirely NULL across
    ``pd_calc`` is dropped.  If both intensity columns are dropped,
    ``pd_calc_overall.component_presentation_order`` is also dropped.

    Parameters
    ----------
    connection
        Open DuckDB connection containing ingested schema tables.
    schema
        Schema descriptor produced by
        :func:`~cifflow.dictionary.schema.generate_schema`.
    clear_source
        If ``True``, delete all rows from ``pd_calc_component`` after
        consolidation (table schema is preserved).

    Returns
    -------
    int
        Number of ``pd_calc`` rows updated with ``component_intensities_net``.

    Raises
    ------
    Exception
        If the transaction cannot be started, or if any SQL statement fails
        (e.g. a schema table is absent from *connection*).  The transaction
        is rolled back before re-raising.
    """
    try:
        diff_ids = [
            r[0] for r in connection.execute(
                'SELECT DISTINCT "diffractogram_id" FROM pd_calc_component'
                ' WHERE "diffractogram_id" IS NOT NULL'
            ).fetchall()
        ]
    except duckdb.Error:
        return 0

    if not diff_ids:
        return 0

    connection.execute('BEGIN TRANSACTION')
    total = 0
    ok = False
    any_real_net = False
    any_real_total = False
    try:
        # Step 1 — Presentation order: sorted distinct phase_ids per diffractogram.
        # DuckDB produces plain JSON; _prefix_sql() re-adds _CONTAINER_PREFIX at
        # DuckDB runtime (via chr(0)||) so quote() renders it as a CIF list.
        raw_pres = connection.execute("""
            SELECT "diffractogram_id",
                   to_json(list("phase_id" ORDER BY "phase_id"))::VARCHAR
            FROM (SELECT DISTINCT "diffractogram_id", "phase_id"
                  FROM pd_calc_component
                  WHERE "diffractogram_id" IS NOT NULL AND "phase_id" IS NOT NULL)
            GROUP BY "diffractogram_id"
        """).fetchall()

        existing_diffs = {
            r[0] for r in connection.execute(
                'SELECT "diffractogram_id" FROM pd_calc_overall'
                ' WHERE "diffractogram_id" IS NOT NULL'
            ).fetchall()
        }
        to_update = [(pres, d) for d, pres in raw_pres if d in existing_diffs]
        to_insert = [(d, pres) for d, pres in raw_pres if d not in existing_diffs]

        if to_update:
            vals_sql = ', '.join(
                f'({_prefix_sql(pres)}, {_sl(d)})' for pres, d in to_update
            )
            connection.execute(f"""
                UPDATE pd_calc_overall
                SET "component_presentation_order" = t.pres
                FROM (VALUES {vals_sql}) AS t(pres, "diffractogram_id")
                WHERE pd_calc_overall."diffractogram_id" = t."diffractogram_id"
            """)
        if to_insert:
            vals_sql = ', '.join(
                f'({_sl(d)}, {_prefix_sql(pres)})' for d, pres in to_insert
            )
            connection.execute(f"""
                INSERT INTO pd_calc_overall ("diffractogram_id", "component_presentation_order")
                SELECT "diffractogram_id", pres
                FROM (VALUES {vals_sql}) AS t("diffractogram_id", pres)
            """)

        # Step 2 — Ensure pd_calc rows exist for every (point_id, diffractogram_id) in component.
        # MIN(_cifflow_block_id) carries a valid block ID so the emit layer can locate the rows.
        connection.execute("""
            INSERT INTO pd_calc ("point_id", "diffractogram_id", "_cifflow_block_id")
            SELECT c."point_id", c."diffractogram_id", MIN(c."_cifflow_block_id")
            FROM pd_calc_component c
            WHERE c."point_id" IS NOT NULL
              AND c."diffractogram_id" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM pd_calc p
                  WHERE p."point_id" = c."point_id"
                    AND p."diffractogram_id" = c."diffractogram_id"
              )
            GROUP BY c."point_id", c."diffractogram_id"
        """)

        # Step 3 — Assemble and write intensity lists, per diffractogram.
        # CROSS JOIN the full phase list against each point so every phase slot is
        # always present (LEFT JOIN fills absent phases with NULL → COALESCE → '.').
        # One batch VALUES UPDATE per diffractogram — avoids per-row execute() calls
        # which are slow on Windows due to AV scanning overhead.
        pres_by_diff = dict(raw_pres)  # diff_id -> plain JSON string (no prefix)

        for diff_id in diff_ids:
            pres_json = pres_by_diff.get(diff_id)
            if not pres_json:
                continue

            rows = connection.execute(f"""
                WITH phases AS (
                    SELECT unnest(from_json({_sl(pres_json)}, '["VARCHAR"]')) AS phase_id,
                           generate_subscripts(from_json({_sl(pres_json)}, '["VARCHAR"]'), 1) AS ord
                ),
                points AS (
                    SELECT DISTINCT "point_id"
                    FROM pd_calc_component
                    WHERE "diffractogram_id" = {_sl(diff_id)}
                      AND "point_id" IS NOT NULL
                )
                SELECT
                    p."point_id",
                    to_json(list(COALESCE(c."intensity_net",   '.') ORDER BY ph.ord))::VARCHAR,
                    to_json(list(COALESCE(c."intensity_total", '.') ORDER BY ph.ord))::VARCHAR
                FROM points p
                CROSS JOIN phases ph
                LEFT JOIN pd_calc_component c
                    ON  c."point_id"         = p."point_id"
                    AND c."diffractogram_id"  = {_sl(diff_id)}
                    AND c."phase_id"          = ph.phase_id
                GROUP BY p."point_id"
            """).fetchall()

            if rows:
                vals_sql = ', '.join(
                    f'({_sl(r[0])}, {_prefix_sql(r[1])}, {_prefix_sql(r[2])})' for r in rows
                )
                n = connection.execute(f"""
                    UPDATE pd_calc
                    SET "component_intensities_net"   = t.net_list,
                        "component_intensities_total"  = t.total_list
                    FROM (VALUES {vals_sql}) AS t("point_id", net_list, total_list)
                    WHERE pd_calc."point_id" = t."point_id"
                      AND pd_calc."diffractogram_id" = {_sl(diff_id)}
                      AND pd_calc."component_intensities_net" IS NULL
                """).fetchone()[0]
                total += n
                any_real_net   = any_real_net   or not all(_all_dot(r[1]) for r in rows)
                any_real_total = any_real_total or not all(_all_dot(r[2]) for r in rows)

            # Fill pd_calc rows that have no component data at all for this diffractogram
            # (the CROSS JOIN only covers points present in pd_calc_component).
            n_phases = len(json.loads(pres_json))
            all_dot = json.dumps(['.'] * n_phases)
            connection.execute(f"""
                UPDATE pd_calc
                SET "component_intensities_net"   = {_prefix_sql(all_dot)},
                    "component_intensities_total"  = {_prefix_sql(all_dot)}
                WHERE "diffractogram_id" = {_sl(diff_id)}
                  AND "component_intensities_net" IS NULL
            """)

        # Step 5 — Clear source values (optional).
        if clear_source:
            connection.execute('DELETE FROM pd_calc_component')

        ok = True
    except Exception:
        raise
    finally:
        if ok:
            connection.execute('COMMIT')
        else:
            try:
                connection.execute('ROLLBACK')
            except duckdb.Error:
                pass

    # Step 4 — Drop all-'.' columns (DDL runs outside the transaction).
    if not any_real_net:
        net_vals = connection.execute(
            'SELECT "component_intensities_net" FROM pd_calc'
            ' WHERE "component_intensities_net" IS NOT NULL'
        ).fetchall()
        if all(_all_dot(r[0]) for r in net_vals):
            connection.execute(
                'ALTER TABLE pd_calc DROP COLUMN "component_intensities_net"'
            )
        else:
            any_real_net = True

    if not any_real_total:
        total_vals = connection.execute(
            'SELECT "component_intensities_total" FROM pd_calc'
            ' WHERE "component_intensities_total" IS NOT NULL'
        ).fetchall()
        if all(_all_dot(r[0]) for r in total_vals):
            connection.execute(
                'ALTER TABLE pd_calc DROP COLUMN "component_intensities_total"'
            )
        else:
            any_real_total = True

    if not any_real_net and not any_real_total:
        connection.execute(
            'ALTER TABLE pd_calc_overall DROP COLUMN "component_presentation_order"'
        )

    return total
