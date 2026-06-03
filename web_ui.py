"""Web-based SQL UI for SQL-DBMS — runs in Docker, accessible via browser."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from dbms import DBMS
from messages import (
    SyntaxError,
    NoSuchTable,
    DuplicateColumnDefError,
    DuplicatePrimaryKeyDefError,
    ReferenceTypeError,
    ReferenceNonPrimaryKeyError,
    ReferenceColumnExistenceError,
    ReferenceTableExistenceError,
    NonExistingColumnDefError,
    TableExistenceError,
    CharLengthError,
    DropReferencedTableError,
    InsertTypeMismatchError,
    InsertColumnExistenceError,
    InsertColumnNonNullableError,
    InsertDuplicatePrimaryKeyError,
    InsertReferentialIntegrityError,
    SelectTableExistenceError,
    SelectColumnResolveError,
    WhereIncomparableError,
    WhereTableNotSpecified,
    WhereColumnNotExist,
    WhereAmbiguousReference,
    DuplicateIndexError,
    NoSuchIndex,
    IndexColumnNotExist,
    NoActiveTransaction,
    TransactionAlreadyActive,
)
from sql_transformer import SQLTransformer
from lark import Lark, Transformer
from lark.exceptions import UnexpectedCharacters, UnexpectedEOF, UnexpectedToken

app = Flask(__name__)

# ── Grammar & Transformer ────────────────────────────────────────────────────
GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
grammar = open(GRAMMAR_PATH).read()
sql_parser = Lark(grammar, start="command", lexer="basic")

# ── DBMS instance (single shared instance for the web UI) ───────────────────
dbms = DBMS()

# ── Query parsing helpers (mirrors run.py) ────────────────────────────────────

def _split_query_sequence(input_query_sequence: str):
    input_query_sequence = input_query_sequence.rstrip()
    query_list = input_query_sequence.split(";")
    return [query.strip() + ";" for query in query_list if query.strip()]


def _parse_query(raw_query: str):
    try:
        parsed = sql_parser.parse(raw_query)
    except Exception:
        raise SyntaxError()
    else:
        transformer = SQLTransformer()
        return transformer.transform(parsed)


def _run_single_query(raw_query: str):
    """Execute one SQL statement and return a dict with the result."""
    statement, table, record, tables, select_columns, where = _parse_query(raw_query)
    statement = str(statement).lower() if statement else ""

    if statement == "select":
        return {
            "type": "table",
            "content": dbms.select(tables, select_columns, where),
        }

    elif statement == "show tables":
        return {"type": "text", "content": dbms.show_tables()}

    elif statement == "create table":
        return {"type": "text", "content": str(dbms.create_table(table))}

    elif statement == "drop table":
        table_name = table["table_name"] if table else None
        return {"type": "text", "content": str(dbms.drop_table(table_name))}

    elif statement in ("explain", "describe", "desc"):
        table_name = table["table_name"] if table else None
        return {"type": "text", "content": str(dbms.explain_describe_desc(table_name))}

    elif statement == "insert":
        return {
            "type": "text",
            "content": str(dbms.insert(table, record)),
        }

    elif statement == "delete":
        table_name = table["table_name"] if table else None
        result, extra = dbms.delete(table_name, where)
        return {"type": "text", "content": str(result) + ("\n" + str(extra) if extra else "")}

    elif statement == "create index":
        return {
            "type": "text",
            "content": str(dbms.create_index(table["table_name"], table["index_name"], table["column_name"])),
        }

    elif statement == "drop index":
        return {
            "type": "text",
            "content": str(dbms.drop_index(table["table_name"], table["index_name"])),
        }

    elif statement == "begin transaction":
        return {"type": "text", "content": str(dbms.begin_transaction())}

    elif statement == "commit":
        return {"type": "text", "content": str(dbms.commit())}

    elif statement == "rollback":
        return {"type": "text", "content": str(dbms.rollback())}

    elif statement == "update":
        table_name = table["table_name"] if table else None
        assignments = record if record else []
        result, extra = dbms.update(table_name, assignments, where)
        return {"type": "text", "content": str(result) + ("\n" + str(extra) if extra else "")}

    elif statement == "exit":
        return {"type": "exit", "content": "Goodbye!"}

    return {"type": "text", "content": "Unsupported statement."}


# ── Result parser for SELECT ─────────────────────────────────────────────────

def _parse_select_output(text: str):
    """Parse dbms.select() text output into {headers, rows}."""
    lines = text.strip().splitlines()
    headers = []
    rows = []
    in_data = False

    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("+") and line.endswith("+"):
            if not headers:
                continue  # first separator
            if not in_data:
                in_data = True
                continue  # separator before data
            in_data = False
            continue
        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p or p == ""]
        if not parts:
            continue
        if not headers:
            headers = parts
        elif in_data:
            rows.append(parts)

    return {"headers": headers, "rows": rows}


# ── Schema helpers ───────────────────────────────────────────────────────────

def _get_tables():
    try:
        output = dbms.show_tables()
        lines = output.strip().splitlines()
        tables = []
        in_tables = False
        for line in lines:
            if line.startswith("-") and len(line) >= 10:
                in_tables = not in_tables
                continue
            if in_tables and line.strip():
                tables.append(line.strip())
        return tables
    except Exception:
        return []


def _get_table_schema(table_name: str):
    try:
        table = dbms.explain_describe_desc(table_name)
        columns = []
        for col_name, col_type in table.columns.items():
            key_str = ""
            if table.primary_key and col_name in table.primary_key:
                key_str = "PRI"
            if table.foreign_keys and col_name in table.foreign_keys:
                key_str = "FOR" if not key_str else "PRI/FOR"
            columns.append(
                {
                    "name": col_name.decode() if isinstance(col_name, bytes) else col_name,
                    "type": col_type,
                    "null": "NO" if col_name in table.not_null_keys else "YES",
                    "key": key_str,
                    "default": "",
                }
            )
        return columns
    except Exception as e:
        return {"error": str(e)}


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/schema")
def api_schema():
    tables = _get_tables()
    return jsonify({"tables": tables})


@app.route("/api/schema/<table_name>")
def api_table_schema(table_name):
    return jsonify(_get_table_schema(table_name))


@app.route("/api/execute", methods=["POST"])
def api_execute():
    data = request.get_json(force=True)
    raw = data.get("query", "").strip()
    if not raw:
        return jsonify({"error": "No query provided."}), 400

    query_list = _split_query_sequence(raw)
    if not query_list:
        return jsonify({"error": "No valid query found."}), 400

    results = []
    for idx, query_str in enumerate(query_list):
        try:
            result = _run_single_query(query_str)
            if result.get("type") == "table":
                result["parsed"] = _parse_select_output(result["content"])
            results.append({"query": query_str, "ok": True, **result})
            if result.get("type") == "exit":
                break
        except (
            SyntaxError,
            NoSuchTable,
            DuplicateColumnDefError,
            DuplicatePrimaryKeyDefError,
            ReferenceTypeError,
            ReferenceNonPrimaryKeyError,
            ReferenceColumnExistenceError,
            ReferenceTableExistenceError,
            NonExistingColumnDefError,
            TableExistenceError,
            CharLengthError,
            DropReferencedTableError,
            InsertTypeMismatchError,
            InsertColumnExistenceError,
            InsertColumnNonNullableError,
            InsertDuplicatePrimaryKeyError,
            InsertReferentialIntegrityError,
            SelectTableExistenceError,
            SelectColumnResolveError,
            WhereIncomparableError,
            WhereTableNotSpecified,
            WhereColumnNotExist,
            WhereAmbiguousReference,
            DuplicateIndexError,
            NoSuchIndex,
            IndexColumnNotExist,
            NoActiveTransaction,
            TransactionAlreadyActive,
            UpdateTypeMismatchError,
            UpdateColumnExistenceError,
            UpdateColumnNonNullableError,
            UpdateDuplicatePrimaryKeyError,
            UpdateReferentialIntegrityError,
        ) as e:
            results.append({"query": query_str, "ok": False, "error": str(e)})
            break  # Stop on first error, matching run.py behavior
        except Exception as e:
            results.append({"query": query_str, "ok": False, "error": str(e)})
            break

    return jsonify({"results": results})


# ── Global error handler ────────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    return jsonify({
        "error": str(e),
        "traceback": traceback.format_exc() if app.debug else None
    }), 500


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
