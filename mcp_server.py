"""
Themison PostgreSQL MCP Server

Gives Claude Code direct access to the local Docker PostgreSQL database
for querying, inspecting, and managing data.

Usage:
    python mcp_server.py

Register with Claude Code:
    claude mcp add --transport stdio --scope project themison-db -- python mcp_server.py
"""

import json
import os

import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP

DATABASE_URL = os.environ.get(
    "MCP_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:54322/postgres",
)

mcp = FastMCP("themison-db")


def _connect():
    """Create a new database connection."""
    return psycopg2.connect(DATABASE_URL)


def _get_public_tables(cur) -> list[str]:
    """Return list of public table names."""
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    return [row[0] for row in cur.fetchall()]


def _validate_table(cur, table_name: str) -> str | None:
    """Validate table exists in public schema. Returns error message or None."""
    tables = _get_public_tables(cur)
    if table_name not in tables:
        return f"Table '{table_name}' not found. Available tables: {', '.join(tables)}"
    return None


def _format_rows(cur) -> str:
    """Format cursor results as a readable table."""
    if cur.description is None:
        return f"OK — {cur.rowcount} row(s) affected"
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    if not rows:
        return "No rows returned."
    # Build list of dicts for JSON output
    result = []
    for row in rows:
        record = {}
        for col, val in zip(columns, row):
            if isinstance(val, (dict, list)):
                record[col] = val
            else:
                record[col] = str(val) if val is not None else None
        result.append(record)
    return json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool 1: list_tables
# ---------------------------------------------------------------------------
@mcp.tool()
def list_tables() -> str:
    """List all tables in the public schema."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            tables = _get_public_tables(cur)
            return f"{len(tables)} tables:\n" + "\n".join(f"  - {t}" for t in tables)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 2: describe_table
# ---------------------------------------------------------------------------
@mcp.tool()
def describe_table(table_name: str) -> str:
    """Describe a table's columns, types, constraints, enum values, and foreign keys.

    Args:
        table_name: Name of the table to describe
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            err = _validate_table(cur, table_name)
            if err:
                return err

            # Columns
            cur.execute(
                """
                SELECT column_name, data_type, udt_name, is_nullable,
                       column_default, character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            columns = cur.fetchall()

            # Primary key columns
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_schema = 'public'
                    AND tc.table_name = %s
                    AND tc.constraint_type = 'PRIMARY KEY'
                """,
                (table_name,),
            )
            pk_cols = {row[0] for row in cur.fetchall()}

            # Foreign keys
            cur.execute(
                """
                SELECT kcu.column_name,
                       ccu.table_name AS foreign_table,
                       ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.table_schema = 'public'
                    AND tc.table_name = %s
                    AND tc.constraint_type = 'FOREIGN KEY'
                """,
                (table_name,),
            )
            fk_rows = cur.fetchall()
            fk_map = {row[0]: f"→ {row[1]}.{row[2]}" for row in fk_rows}

            # Enum values for USER-DEFINED types
            enum_map = {}
            udt_names = {c[2] for c in columns if c[1] == "USER-DEFINED"}
            for udt in udt_names:
                cur.execute(
                    """
                    SELECT e.enumlabel
                    FROM pg_enum e
                    JOIN pg_type t ON e.enumtypid = t.oid
                    WHERE t.typname = %s
                    ORDER BY e.enumsortorder
                    """,
                    (udt,),
                )
                enum_map[udt] = [row[0] for row in cur.fetchall()]

            # Format output
            lines = [f"Table: {table_name}", ""]
            lines.append("Columns:")
            for col_name, data_type, udt_name, nullable, default, max_len in columns:
                type_str = data_type
                if data_type == "USER-DEFINED":
                    type_str = udt_name
                    if udt_name in enum_map:
                        type_str += f" ({', '.join(enum_map[udt_name])})"
                elif data_type == "character varying" and max_len:
                    type_str = f"varchar({max_len})"

                parts = [f"  {col_name}: {type_str}"]
                if col_name in pk_cols:
                    parts.append("PK")
                if nullable == "NO":
                    parts.append("NOT NULL")
                if default:
                    parts.append(f"default={default}")
                if col_name in fk_map:
                    parts.append(fk_map[col_name])
                lines.append(" | ".join(parts))

            if fk_rows:
                lines.append("")
                lines.append("Foreign Keys:")
                for col, ftable, fcol in fk_rows:
                    lines.append(f"  {col} → {ftable}.{fcol}")

            return "\n".join(lines)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 3: query_table
# ---------------------------------------------------------------------------
@mcp.tool()
def query_table(
    table_name: str,
    columns: str = "*",
    where: str = "",
    order_by: str = "",
    limit: int = 50,
) -> str:
    """SELECT rows from a table with optional filters.

    Args:
        table_name: Name of the table to query
        columns: Comma-separated column names or * for all (default: *)
        where: SQL WHERE clause without the WHERE keyword (e.g. "status = 'active'")
        order_by: SQL ORDER BY clause without ORDER BY keyword (e.g. "created_at DESC")
        limit: Max rows to return, capped at 500 (default: 50)
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            err = _validate_table(cur, table_name)
            if err:
                return err

            limit = min(limit, 500)
            sql = f'SELECT {columns} FROM "{table_name}"'
            if where:
                sql += f" WHERE {where}"
            if order_by:
                sql += f" ORDER BY {order_by}"
            sql += f" LIMIT {limit}"

            try:
                cur.execute(sql)
                return _format_rows(cur)
            except Exception as e:
                conn.rollback()
                return f"Query error: {e}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 4: insert_record
# ---------------------------------------------------------------------------
@mcp.tool()
def insert_record(table_name: str, data: dict) -> str:
    """Insert a record into a table. Returns the inserted row.

    Args:
        table_name: Name of the table
        data: Dict of column_name → value pairs to insert
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            err = _validate_table(cur, table_name)
            if err:
                return err

            cols = list(data.keys())
            vals = []
            for v in data.values():
                if isinstance(v, (dict, list)):
                    vals.append(psycopg2.extras.Json(v))
                else:
                    vals.append(v)

            col_str = ", ".join(f'"{c}"' for c in cols)
            placeholder_str = ", ".join(["%s"] * len(cols))
            sql = f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholder_str}) RETURNING *'

            try:
                cur.execute(sql, vals)
                conn.commit()
                return _format_rows(cur)
            except Exception as e:
                conn.rollback()
                return f"Insert error: {e}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 5: update_record
# ---------------------------------------------------------------------------
@mcp.tool()
def update_record(table_name: str, data: dict, where: str) -> str:
    """Update records in a table. WHERE clause is required for safety.

    Args:
        table_name: Name of the table
        data: Dict of column_name → new_value pairs
        where: SQL WHERE clause without WHERE keyword (required, e.g. "id = 'abc-123'")
    """
    if not where.strip():
        return "Error: WHERE clause is required for updates."
    conn = _connect()
    try:
        with conn.cursor() as cur:
            err = _validate_table(cur, table_name)
            if err:
                return err

            set_parts = []
            vals = []
            for col, val in data.items():
                set_parts.append(f'"{col}" = %s')
                if isinstance(val, (dict, list)):
                    vals.append(psycopg2.extras.Json(val))
                else:
                    vals.append(val)

            sql = f'UPDATE "{table_name}" SET {", ".join(set_parts)} WHERE {where} RETURNING *'

            try:
                cur.execute(sql, vals)
                conn.commit()
                return _format_rows(cur)
            except Exception as e:
                conn.rollback()
                return f"Update error: {e}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 6: delete_record
# ---------------------------------------------------------------------------
@mcp.tool()
def delete_record(table_name: str, where: str) -> str:
    """Delete records from a table. WHERE clause is required for safety.

    Args:
        table_name: Name of the table
        where: SQL WHERE clause without WHERE keyword (required, e.g. "id = 'abc-123'")
    """
    if not where.strip():
        return "Error: WHERE clause is required for deletes."
    conn = _connect()
    try:
        with conn.cursor() as cur:
            err = _validate_table(cur, table_name)
            if err:
                return err

            sql = f'DELETE FROM "{table_name}" WHERE {where} RETURNING *'

            try:
                cur.execute(sql)
                conn.commit()
                return _format_rows(cur)
            except Exception as e:
                conn.rollback()
                return f"Delete error: {e}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 7: run_sql
# ---------------------------------------------------------------------------
@mcp.tool()
def run_sql(sql: str) -> str:
    """Execute arbitrary SQL (SELECT, INSERT, UPDATE, DELETE, DDL, JOINs, CTEs, etc).

    Args:
        sql: The SQL statement to execute
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql)
                if cur.description is not None:
                    return _format_rows(cur)
                else:
                    conn.commit()
                    return f"OK — {cur.rowcount} row(s) affected"
            except Exception as e:
                conn.rollback()
                return f"SQL error: {e}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
