"""Quick integration test for the basic indexing feature.

Run from the project root (the folder containing SQL-DBMS/):
    cd SQL-DBMS
    python test_index.py
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


def test_indexing():
    # Clean up any existing DB
    db_dir = os.path.join(_SCRIPT_DIR, "DB")
    if os.path.exists(db_dir):
        try:
            shutil.rmtree(db_dir)
        except OSError:
            # DB may be a Docker volume mount point; remove contents only
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

    print("\n=== 1. CREATE TABLE ===")
    r = dbms.create_table({
        "table_name": "users",
        "column_list": [("id", "int"), ("name", "char(20)"), ("age", "int")],
        "not_null_key_set": {"id", "name"},
        "primary_key_list": [("id",)],
        "foreign_key_dict": {}
    })
    assert_eq(str(r), "'users' table is created", "create table")

    print("\n=== 2. INSERT records ===")
    dbms.insert({"table_name": "users", "column_name_list": None}, [1, "Alice", 30])
    dbms.insert({"table_name": "users", "column_name_list": None}, [2, "Bob", 25])
    dbms.insert({"table_name": "users", "column_name_list": None}, [3, "Charlie", 35])
    dbms.insert({"table_name": "users", "column_name_list": None}, [4, "Diana", 25])
    print("  [PASS] inserted 4 rows")
    passed += 1

    print("\n=== 3. CREATE INDEX ===")
    r = dbms.create_index("users", "idx_age", "age")
    assert_eq(str(r), "Index 'idx_age' is created", "create index")

    print("\n=== 4. SELECT with index (equality) ===")
    out = dbms.select(["users"], [], {
        "op": "=",
        "left_operand": (None, "age"),
        "right_operand": (25,)
    })
    assert_in(out, "Bob", "select equality finds Bob")
    assert_in(out, "Diana", "select equality finds Diana")
    # Alice has age=30, so she should NOT appear in age=25 results
    if "Alice" not in out:
        passed += 1
        print("  [PASS] select equality correctly excludes Alice")
    else:
        failed += 1
        print("  [FAIL] select equality should exclude Alice (age=30)")

    print("\n=== 5. SELECT with index (range) ===")
    out = dbms.select(["users"], [], {
        "op": "<",
        "left_operand": (None, "age"),
        "right_operand": (30,)
    })
    assert_in(out, "Bob", "range select finds Bob")
    assert_in(out, "Diana", "range select finds Diana")
    # Should NOT contain Alice (age=30) or Charlie (age=35)
    if "Alice" not in out and "Charlie" not in out:
        passed += 1
        print("  [PASS] range select correctly excludes Alice and Charlie")
    else:
        failed += 1
        print("  [FAIL] range select should exclude Alice and Charlie")

    print("\n=== 6. DELETE with index ===")
    r, extra = dbms.delete("users", {
        "op": "=",
        "left_operand": (None, "age"),
        "right_operand": (25,)
    })
    assert_eq(str(r), "'2' row(s) are deleted", "delete removes 2 rows")

    print("\n=== 7. VERIFY remaining records ===")
    out = dbms.select(["users"], [], None)
    assert_in(out, "Alice", "Alice still present")
    assert_in(out, "Charlie", "Charlie still present")
    if "Bob" not in out and "Diana" not in out:
        passed += 1
        print("  [PASS] Bob and Diana correctly removed")
    else:
        failed += 1
        print("  [FAIL] Bob and Diana should have been removed")

    print("\n=== 8. DROP INDEX ===")
    r = dbms.drop_index("users", "idx_age")
    assert_eq(str(r), "Index 'idx_age' is dropped", "drop index")

    print("\n=== 9. DROP TABLE ===")
    r = dbms.drop_table("users")
    assert_eq(str(r), "'users' table is dropped", "drop table")

    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed!")
    else:
        print("Some tests failed.")
    return failed == 0


if __name__ == "__main__":
    success = test_indexing()
    sys.exit(0 if success else 1)
