"""Integration tests for the SQL transaction feature (BEGIN, COMMIT, ROLLBACK).

Run from the project root:
    cd SQL-DBMS
    python test_transaction.py
"""
import os
import sys
import shutil

# Ensure project modules are importable
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from dbms import DBMS
from messages import *


def test_transaction():
    # Clean up any existing DB
    db_dir = os.path.join(_SCRIPT_DIR, "DB")
    if os.path.exists(db_dir):
        try:
            shutil.rmtree(db_dir)
        except OSError:
            for item in os.listdir(db_dir):
                item_path = os.path.join(db_dir, item)
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)

    dbms = DBMS()
    passed = 0
    failed = 0

    def assert_eq(got, expected, msg):
        nonlocal passed, failed
        if got == expected:
            passed += 1
            print(f"  [PASS] {msg}")
        else:
            failed += 1
            print(f"  [FAIL] {msg}\n    expected: {expected!r}\n    got:      {got!r}")

    def assert_in(text, substring, msg):
        nonlocal passed, failed
        if substring in text:
            passed += 1
            print(f"  [PASS] {msg}")
        else:
            failed += 1
            print(f"  [FAIL] {msg}\n    '{substring}' not found in output")

    def assert_not_in(text, substring, msg):
        nonlocal passed, failed
        if substring not in text:
            passed += 1
            print(f"  [PASS] {msg}")
        else:
            failed += 1
            print(f"  [FAIL] {msg}\n    '{substring}' unexpectedly found in output")

    def assert_raises(exc_type, callable, msg):
        nonlocal passed, failed
        try:
            callable()
            failed += 1
            print(f"  [FAIL] {msg}\n    expected {exc_type.__name__} was not raised")
        except exc_type:
            passed += 1
            print(f"  [PASS] {msg}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {msg}\n    expected {exc_type.__name__}, got {type(e).__name__}: {e}")

    # =================================================================== #
    #  1. Setup tables                                                    #
    # =================================================================== #
    print("\n=== 1. CREATE TABLES ===")
    r = dbms.create_table({
        "table_name": "accounts",
        "column_list": [("id", "int"), ("name", "char(20)"), ("balance", "int")],
        "not_null_key_set": {"id", "name"},
        "primary_key_list": [("id",)],
        "foreign_key_dict": {}
    })
    assert_eq(str(r), "'accounts' table is created", "create accounts table")

    r = dbms.create_table({
        "table_name": "transactions",
        "column_list": [("tx_id", "int"), ("account_id", "int"), ("amount", "int")],
        "not_null_key_set": {"tx_id", "account_id"},
        "primary_key_list": [("tx_id",)],
        "foreign_key_dict": {"account_id": ("accounts", "id")}
    })
    assert_eq(str(r), "'transactions' table is created", "create transactions table")

    # Seed data
    dbms.insert({"table_name": "accounts", "column_name_list": None}, [1, "Alice", 100])
    dbms.insert({"table_name": "accounts", "column_name_list": None}, [2, "Bob", 200])
    print("  [PASS] seeded 2 accounts")
    passed += 1

    # =================================================================== #
    #  2. Basic BEGIN / COMMIT                                            #
    # =================================================================== #
    print("\n=== 2. BASIC BEGIN / COMMIT ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin transaction")

    r = dbms.insert({"table_name": "accounts", "column_name_list": None}, [3, "Charlie", 300])
    assert_eq(str(r), "The row is inserted", "insert during transaction")

    # Select should see the uncommitted row
    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Charlie", "select sees uncommitted insert (read-your-own-writes)")

    r = dbms.commit()
    assert_eq(str(r), "Transaction committed", "commit transaction")

    # After commit, the row should persist
    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Charlie", "committed row persists after commit")

    # =================================================================== #
    #  3. BEGIN / ROLLBACK                                                #
    # =================================================================== #
    print("\n=== 3. BASIC BEGIN / ROLLBACK ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin transaction for rollback test")

    r = dbms.insert({"table_name": "accounts", "column_name_list": None}, [4, "Diana", 400])
    assert_eq(str(r), "The row is inserted", "insert before rollback")

    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Diana", "select sees row before rollback")

    r = dbms.rollback()
    assert_eq(str(r), "Transaction rolled back", "rollback transaction")

    # After rollback, Diana should not exist
    out = dbms.select(["accounts"], [], None)
    assert_not_in(out, "Diana", "rolled-back row is gone")
    assert_in(out, "Charlie", "committed row still present after rollback")

    # =================================================================== #
    #  4. DELETE in transaction                                           #
    # =================================================================== #
    print("\n=== 4. DELETE IN TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for delete test")

    r, extra = dbms.delete("accounts", {
        "op": "=",
        "left_operand": (None, "id"),
        "right_operand": (3,)
    })
    assert_eq(str(r), "'1' row(s) are deleted", "delete during transaction")

    out = dbms.select(["accounts"], [], None)
    assert_not_in(out, "Charlie", "select does not see deleted row in transaction")
    assert_in(out, "Alice", "other rows still visible")

    r = dbms.commit()
    assert_eq(str(r), "Transaction committed", "commit delete")

    out = dbms.select(["accounts"], [], None)
    assert_not_in(out, "Charlie", "deleted row stays gone after commit")

    # =================================================================== #
    #  5. CREATE TABLE in transaction                                     #
    # =================================================================== #
    print("\n=== 5. CREATE TABLE IN TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for create table test")

    r = dbms.create_table({
        "table_name": "logs",
        "column_list": [("log_id", "int"), ("message", "char(50)")],
        "not_null_key_set": {"log_id"},
        "primary_key_list": [("log_id",)],
        "foreign_key_dict": {}
    })
    assert_eq(str(r), "'logs' table is created", "create table in transaction")

    # Insert into newly created table within the same transaction
    r = dbms.insert({"table_name": "logs", "column_name_list": None}, [1, "startup"])
    assert_eq(str(r), "The row is inserted", "insert into tx-created table")

    out = dbms.select(["logs"], [], None)
    assert_in(out, "startup", "select from tx-created table")

    # SHOW TABLES should include the new table
    out = dbms.show_tables()
    assert_in(out, "logs", "show tables includes tx-created table")

    r = dbms.commit()
    assert_eq(str(r), "Transaction committed", "commit create table")

    out = dbms.select(["logs"], [], None)
    assert_in(out, "startup", "tx-created table persists after commit")

    # =================================================================== #
    #  6. DROP TABLE in transaction                                       #
    # =================================================================== #
    print("\n=== 6. DROP TABLE IN TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for drop table test")

    r = dbms.drop_table("logs")
    assert_eq(str(r), "'logs' table is dropped", "drop table in transaction")

    # SHOW TABLES should not include the dropped table
    out = dbms.show_tables()
    assert_not_in(out, "logs", "show tables excludes tx-dropped table")

    # EXPLAIN should fail for dropped table
    assert_raises(NoSuchTable, lambda: dbms.explain_describe_desc("logs"), "explain fails for tx-dropped table")

    r = dbms.rollback()
    assert_eq(str(r), "Transaction rolled back", "rollback drop table")

    # After rollback, the table should still exist
    out = dbms.select(["logs"], [], None)
    assert_in(out, "startup", "rolled-back drop table still exists")

    # =================================================================== #
    #  7. Multiple operations in one transaction                            #
    # =================================================================== #
    print("\n=== 7. MULTIPLE OPS IN ONE TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin multi-op transaction")

    dbms.insert({"table_name": "accounts", "column_name_list": None}, [5, "Eve", 500])
    dbms.insert({"table_name": "accounts", "column_name_list": None}, [6, "Frank", 600])
    r, extra = dbms.delete("accounts", {
        "op": "=",
        "left_operand": (None, "id"),
        "right_operand": (1,)
    })
    assert_eq(str(r), "'1' row(s) are deleted", "delete in multi-op tx")

    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Eve", "multi-op tx: Eve visible")
    assert_in(out, "Frank", "multi-op tx: Frank visible")
    assert_not_in(out, "Alice", "multi-op tx: Alice deleted")

    r = dbms.commit()
    assert_eq(str(r), "Transaction committed", "commit multi-op transaction")

    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Eve", "multi-op commit: Eve persists")
    assert_in(out, "Frank", "multi-op commit: Frank persists")
    assert_not_in(out, "Alice", "multi-op commit: Alice stays deleted")

    # =================================================================== #
    #  8. Error: BEGIN when already active                                #
    # =================================================================== #
    print("\n=== 8. ERROR: BEGIN WHEN ACTIVE ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin first transaction")

    assert_raises(TransactionAlreadyActive, dbms.begin_transaction, "begin twice raises error")

    dbms.rollback()  # clean up

    # =================================================================== #
    #  9. Error: COMMIT / ROLLBACK without BEGIN                          #
    # =================================================================== #
    print("\n=== 9. ERROR: COMMIT/ROLLBACK WITHOUT BEGIN ===")
    assert_raises(NoActiveTransaction, dbms.commit, "commit without begin raises error")
    assert_raises(NoActiveTransaction, dbms.rollback, "rollback without begin raises error")

    # =================================================================== #
    #  10. Primary key uniqueness in transaction                          #
    # =================================================================== #
    print("\n=== 10. PRIMARY KEY UNIQUENESS IN TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for PK test")

    dbms.insert({"table_name": "accounts", "column_name_list": None}, [7, "Grace", 700])

    # Duplicate PK should fail even within the same transaction
    assert_raises(InsertDuplicatePrimaryKeyError,
                  lambda: dbms.insert({"table_name": "accounts", "column_name_list": None}, [7, "Grace2", 700]),
                  "duplicate PK in same transaction raises error")

    dbms.rollback()

    # =================================================================== #
    #  11. Foreign key validation in transaction                          #
    # =================================================================== #
    print("\n=== 11. FOREIGN KEY VALIDATION IN TRANSACTION ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for FK test")

    # Inserting a transaction referencing non-existent account should fail
    assert_raises(InsertReferentialIntegrityError,
                  lambda: dbms.insert({"table_name": "transactions", "column_name_list": None}, [1, 999, 50]),
                  "FK violation in transaction raises error")

    # Inserting a transaction referencing existing account should succeed
    r = dbms.insert({"table_name": "transactions", "column_name_list": None}, [1, 2, 50])
    assert_eq(str(r), "The row is inserted", "valid FK insert in transaction")

    dbms.commit()

    out = dbms.select(["transactions"], [], None)
    assert_in(out, "50", "committed FK row persists")

    # =================================================================== #
    #  12. Insert-then-delete in same transaction                         #
    # =================================================================== #
    print("\n=== 12. INSERT-THEN-DELETE IN SAME TX ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for insert-delete test")

    dbms.insert({"table_name": "accounts", "column_name_list": None}, [8, "Hank", 800])
    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Hank", "Hank visible after insert")

    r, extra = dbms.delete("accounts", {
        "op": "=",
        "left_operand": (None, "id"),
        "right_operand": (8,)
    })
    assert_eq(str(r), "'1' row(s) are deleted", "delete Hank in same tx")

    out = dbms.select(["accounts"], [], None)
    assert_not_in(out, "Hank", "Hank gone after delete in same tx")

    r = dbms.commit()
    assert_eq(str(r), "Transaction committed", "commit insert-delete tx")

    out = dbms.select(["accounts"], [], None)
    assert_not_in(out, "Hank", "Hank stays gone after commit")

    # =================================================================== #
    #  13. Delete-then-insert in same transaction                         #
    # =================================================================== #
    print("\n=== 13. DELETE-THEN-INSERT IN SAME TX ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for delete-insert test")

    r, extra = dbms.delete("accounts", {
        "op": "=",
        "left_operand": (None, "id"),
        "right_operand": (5,)
    })
    assert_eq(str(r), "'1' row(s) are deleted", "delete Eve")

    dbms.insert({"table_name": "accounts", "column_name_list": None}, [5, "Evelyn", 550])
    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Evelyn", "re-insert with same PK after delete works")
    assert_not_in(out, "| Eve |", "old Eve gone")

    dbms.commit()

    out = dbms.select(["accounts"], [], None)
    assert_in(out, "Evelyn", "re-insert persists after commit")

    # =================================================================== #
    #  14. Rollback of CREATE TABLE                                       #
    # =================================================================== #
    print("\n=== 14. ROLLBACK OF CREATE TABLE ===")
    r = dbms.begin_transaction()
    assert_eq(str(r), "Transaction started", "begin for create-rollback test")

    r = dbms.create_table({
        "table_name": "temp_table",
        "column_list": [("x", "int")],
        "not_null_key_set": {"x"},
        "primary_key_list": [("x",)],
        "foreign_key_dict": {}
    })
    assert_eq(str(r), "'temp_table' table is created", "create temp table in tx")

    dbms.rollback()

    assert_raises(NoSuchTable, lambda: dbms.explain_describe_desc("temp_table"), "rolled-back create table does not exist")

    # =================================================================== #
    #  15. Cleanup                                                        #
    # =================================================================== #
    print("\n=== 15. CLEANUP ===")
    dbms.drop_table("transactions")
    dbms.drop_table("accounts")
    dbms.drop_table("logs")
    print("  [PASS] dropped all test tables")
    passed += 1

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed!")
    else:
        print("Some tests failed.")
    return failed == 0


if __name__ == "__main__":
    success = test_transaction()
    sys.exit(0 if success else 1)
