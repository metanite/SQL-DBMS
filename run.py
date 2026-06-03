import re
import sys

from lark import Lark

from dbms import DBMS
from messages import *
from sql_transformer import SQLTransformer

PROMPT = "DB_2023-12345> "  # personal information

dbms = DBMS()

# --------------------------------------------------------------------------- #
#  SQL comment stripping (parser does not handle comments)                    #
# --------------------------------------------------------------------------- #

def strip_sql_comments(sql_text: str) -> str:
    """Remove SQL-style comments (-- line, # line, /* block */) from text."""
    # Block comments /* ... */
    sql_text = re.sub(r"/\*.*?\*/", " ", sql_text, flags=re.DOTALL)
    # Line comments -- ... and # ...
    sql_text = re.sub(r"--[^\n]*", "", sql_text)
    sql_text = re.sub(r"#[^\n]*", "", sql_text)
    return sql_text


def split_query_sequence(input_query_sequence: str):
    """Split a semicolon-terminated query sequence into individual statements."""
    input_query_sequence = input_query_sequence.rstrip()
    query_list = input_query_sequence.split(";")
    return [query.strip() + ";" for query in query_list if query.strip()]


def load_sql_file(file_path: str):
    """Read a SQL file, strip comments, and return a list of query strings."""
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
    cleaned = strip_sql_comments(raw)
    queries = split_query_sequence(cleaned)
    return queries


# --------------------------------------------------------------------------- #
#  Query parsing                                                              #
# --------------------------------------------------------------------------- #

def parse_query(sql_parser: Lark, sql_transformer, query):
    """Parses the query and returns the transformed parse tree."""
    try:
        parsed = sql_parser.parse(query)
    except Exception:
        raise SyntaxError()
    else:
        transformed = sql_transformer.transform(parsed)
        return transformed


def parse_query_sequence(input_query_sequence: str):
    """Parses the input query sequence and returns a list of queries."""
    while True:
        input_query_sequence = input_query_sequence.rstrip()  # remove any trailing whitespaces after the semicolon
        if input_query_sequence.endswith(";"):  # end of query sequence
            break
        else:
            input_query_sequence += " " + input()  # waits for any additional input until the semicolon is found
    return split_query_sequence(input_query_sequence)


# --------------------------------------------------------------------------- #
#  Shared execution logic                                                     #
# --------------------------------------------------------------------------- #

def execute_single(sql_parser: Lark, query: str):
    """Execute one SQL statement and return (ok, result_or_error, is_exit).
    
    ok: bool — True if execution succeeded, False if an error occurred.
    result_or_error: str — the output message or error message.
    is_exit: bool — True if the statement was an exit command.
    """
    sql_transformer = SQLTransformer()
    statement, table, record, tables, select_columns, where = parse_query(
        sql_parser, sql_transformer, query
    )

    if statement == "exit":
        return True, "Goodbye!", True

    if statement == "create table":
        success = dbms.create_table(table)
        return True, str(success), False

    elif statement == "drop table":
        success = dbms.drop_table(table["table_name"])
        return True, str(success), False

    elif statement in ("explain", "describe", "desc"):
        tbl = dbms.explain_describe_desc(table["table_name"])
        return True, str(tbl), False

    elif statement == "show tables":
        output = dbms.show_tables()
        return True, output, False

    elif statement == "insert":
        result = dbms.insert(table, record)
        return True, str(result), False

    elif statement == "delete":
        result, extra = dbms.delete(table["table_name"], where)
        lines = str(result)
        if extra:
            lines += "\n" + str(extra)
        return True, lines, False

    elif statement == "select":
        output = dbms.select(tables, select_columns, where)
        return True, output, False

    elif statement == "create index":
        success = dbms.create_index(
            table["table_name"], table["index_name"], table["column_name"]
        )
        return True, str(success), False

    elif statement == "drop index":
        success = dbms.drop_index(table["table_name"], table["index_name"])
        return True, str(success), False

    elif statement == "begin transaction":
        result = dbms.begin_transaction()
        return True, str(result), False

    elif statement == "commit":
        result = dbms.commit()
        return True, str(result), False

    elif statement == "rollback":
        result = dbms.rollback()
        return True, str(result), False

    elif statement == "update":
        result, extra = dbms.update(table["table_name"], record, where)
        lines = str(result)
        if extra:
            lines += "\n" + str(extra)
        return True, lines, False

    return True, f"Unsupported statement: {statement}", False


# --------------------------------------------------------------------------- #
#  File execution                                                             #
# --------------------------------------------------------------------------- #

def run_sql_file(sql_parser: Lark, file_path: str, stop_on_error: bool = False):
    """Execute all statements in a SQL file.

    Parameters
    ----------
    sql_parser : Lark
        The Lark SQL parser instance.
    file_path : str
        Path to the .sql file.
    stop_on_error : bool
        If True, abort on the first error. If False, log the error and continue.
    """
    try:
        queries = load_sql_file(file_path)
    except FileNotFoundError:
        print(PROMPT + f"Error: file not found '{file_path}'")
        return False
    except Exception as e:
        print(PROMPT + f"Error reading file: {e}")
        return False

    if not queries:
        print(PROMPT + "No valid SQL statements found in file.")
        return True

    print(PROMPT + f"-- Loading {file_path} ({len(queries)} statement(s))")

    success_count = 0
    error_count = 0

    for query in queries:
        try:
            ok, result, is_exit = execute_single(sql_parser, query)
            if is_exit:
                print(PROMPT + result)
                break
            print(PROMPT + result)
            success_count += 1
        except (
            SyntaxError, NoSuchTable, DuplicateColumnDefError,
            DuplicatePrimaryKeyDefError, ReferenceTypeError,
            ReferenceNonPrimaryKeyError, ReferenceColumnExistenceError,
            ReferenceTableExistenceError, NonExistingColumnDefError,
            TableExistenceError, CharLengthError, DropReferencedTableError,
            InsertTypeMismatchError, InsertColumnExistenceError,
            InsertColumnNonNullableError, InsertDuplicatePrimaryKeyError,
            InsertReferentialIntegrityError, SelectTableExistenceError,
            SelectColumnResolveError, WhereIncomparableError,
            WhereTableNotSpecified, WhereColumnNotExist, WhereAmbiguousReference,
            DuplicateIndexError, NoSuchIndex, IndexColumnNotExist,
            NoActiveTransaction, TransactionAlreadyActive,
            UpdateTypeMismatchError, UpdateColumnExistenceError,
            UpdateColumnNonNullableError, UpdateDuplicatePrimaryKeyError,
            UpdateReferentialIntegrityError,
        ) as e:
            print(PROMPT + str(e))
            error_count += 1
            if stop_on_error:
                break
        except Exception as e:
            print(PROMPT + f"Unexpected error: {e}")
            error_count += 1
            if stop_on_error:
                break

    print(
        PROMPT
        + f"-- Done: {success_count} succeeded, {error_count} failed"
    )
    return error_count == 0


# --------------------------------------------------------------------------- #
#  Main entry point                                                           #
# --------------------------------------------------------------------------- #

def main():
    with open("grammar.lark") as file:
        sql_parser = Lark(file.read(), start="command", lexer="basic")

    # ── Command-line file mode ──────────────────────────────────────────────
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        run_sql_file(sql_parser, file_path, stop_on_error=False)
        return

    # ── Interactive REPL mode ───────────────────────────────────────────────
    exit_flag = False
    while not exit_flag:
        user_input = input(PROMPT)

        # Handle \source command inside the REPL
        if user_input.strip().lower().startswith("\\source"):
            parts = user_input.strip().split(None, 1)
            if len(parts) < 2:
                print(PROMPT + "Usage: \\source <filename.sql>")
                continue
            file_path = parts[1].strip().strip("\"'")
            run_sql_file(sql_parser, file_path, stop_on_error=False)
            continue

        query_list = parse_query_sequence(user_input)
        for query in query_list:
            try:
                ok, result, is_exit = execute_single(sql_parser, query)
                if is_exit:
                    exit_flag = True
                    print(PROMPT + result)
                    break
                print(PROMPT + result)
            except (
                SyntaxError, NoSuchTable, DuplicateColumnDefError,
                DuplicatePrimaryKeyDefError, ReferenceTypeError,
                ReferenceNonPrimaryKeyError, ReferenceColumnExistenceError,
                ReferenceTableExistenceError, NonExistingColumnDefError,
                TableExistenceError, CharLengthError, DropReferencedTableError,
                InsertTypeMismatchError, InsertColumnExistenceError,
                InsertColumnNonNullableError, InsertDuplicatePrimaryKeyError,
                InsertReferentialIntegrityError, SelectTableExistenceError,
                SelectColumnResolveError, WhereIncomparableError,
                WhereTableNotSpecified, WhereColumnNotExist, WhereAmbiguousReference,
                DuplicateIndexError, NoSuchIndex, IndexColumnNotExist,
                NoActiveTransaction, TransactionAlreadyActive,
                UpdateTypeMismatchError, UpdateColumnExistenceError,
                UpdateColumnNonNullableError, UpdateDuplicatePrimaryKeyError,
                UpdateReferentialIntegrityError,
            ) as e:
                print(PROMPT + str(e))
                break


if __name__ == "__main__":
    main()
