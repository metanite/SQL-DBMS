from pathlib import Path
from typing import Dict, List
import itertools
from collections import Counter
from copy import deepcopy

from db_model import Table, Record, DB, MetaDB
from btree import BTreeIndex
from utils import *
from messages import *



class DBMS:
    def __init__(self):
        self.db_dir = Path("./DB")
        self.db_dir.mkdir(exist_ok=True)
        self.meta_db = MetaDB()

        # Transaction state
        self.tx_active = False
        self.tx_operations = []  # Sequential log of buffered operations
        self.tx_tables_created = {}  # table_name -> table_dict
        self.tx_tables_dropped = set()
        self.tx_indexes_created = {}  # table_name -> [(index_name, column_name)]
        self.tx_indexes_dropped = {}  # table_name -> [index_name]
        
        
    # --------------------------------------------------------------------- #
    #                         Transaction Control                             #
    # --------------------------------------------------------------------- #

    def begin_transaction(self):
        if self.tx_active:
            raise TransactionAlreadyActive()
        self.tx_active = True
        self.tx_operations = []
        self.tx_tables_created = {}
        self.tx_tables_dropped = set()
        self.tx_indexes_created = {}
        self.tx_indexes_dropped = {}
        return BeginTransactionSuccess()

    def commit(self):
        if not self.tx_active:
            raise NoActiveTransaction()
        # Replay buffered operations in order
        for op in self.tx_operations:
            op_type = op[0]
            if op_type == "insert":
                self._do_insert(op[1], op[2])
            elif op_type == "delete":
                self._do_delete(op[1], op[2])
            elif op_type == "create_table":
                self._do_create_table(op[1])
            elif op_type == "drop_table":
                self._do_drop_table(op[1])
            elif op_type == "create_index":
                self._do_create_index(op[1], op[2], op[3])
            elif op_type == "drop_index":
                self._do_drop_index(op[1], op[2])
            elif op_type == "update":
                self._do_update(op[1], op[2], op[3])
        self._clear_transaction()
        return CommitSuccess()

    def rollback(self):
        if not self.tx_active:
            raise NoActiveTransaction()
        self._clear_transaction()
        return RollbackSuccess()

    def _clear_transaction(self):
        self.tx_active = False
        self.tx_operations = []
        self.tx_tables_created = {}
        self.tx_tables_dropped = set()
        self.tx_indexes_created = {}
        self.tx_indexes_dropped = {}

    # --------------------------------------------------------------------- #
    #                    Transaction-aware helpers                          #
    # --------------------------------------------------------------------- #

    def _get_table_schema(self, table_name: str):
        """Return the Table schema, considering pending transaction changes."""
        if table_name in self.tx_tables_dropped:
            return None
        if table_name in self.tx_tables_created:
            return self._table_from_dict(self.tx_tables_created[table_name])

        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        self.meta_db.close_db()
        if table is None:
            return None

        # Apply pending index changes to the schema copy
        if table_name in self.tx_indexes_created or table_name in self.tx_indexes_dropped:
            import copy
            table = copy.deepcopy(table)
            if not hasattr(table, "indexes"):
                table.indexes = {}
            if table_name in self.tx_indexes_created:
                for index_name, column_name in self.tx_indexes_created[table_name]:
                    table.indexes[index_name] = {"column": column_name, "file": f"{table_name}_{index_name}.idx"}
            if table_name in self.tx_indexes_dropped:
                for index_name in self.tx_indexes_dropped[table_name]:
                    if index_name in table.indexes:
                        del table.indexes[index_name]
        return table

    def _table_from_dict(self, table_dict: dict):
        """Build a Table object from a transformer table_dict."""
        columns = {name: dtype for name, dtype in table_dict["column_list"]}
        primary_key = table_dict["primary_key_list"][0] if table_dict["primary_key_list"] else None
        return Table(
            table_name=table_dict["table_name"],
            columns=columns,
            not_null_keys=table_dict["not_null_key_set"],
            primary_key=primary_key,
            foreign_keys=table_dict["foreign_key_dict"],
        )

    def _get_table_records(self, table_name: str):
        """Return all records in the current transaction view as {key: data_dict}."""
        table = self._get_table_schema(table_name)
        if table is None:
            raise NoSuchTable()

        records = {}  # key -> data dict

        # Load committed records only if table exists on disk
        if table_name not in self.tx_tables_created:
            table_db = DB(table_name)
            table_db.open_db()
            cursor = table_db.create_cursor()
            pair = cursor.first()
            while pair:
                key, value = pair
                record = Record.deserialize(value)
                records[key] = record.data
                pair = cursor.next()
            table_db.discard_cursor(cursor)
            table_db.close_db()

        # Apply buffered operations in order
        for op in self.tx_operations:
            if op[0] == "insert" and op[1]["table_name"] == table_name:
                _, tdict, values = op
                data = {}
                for col_name, value in zip(table.columns.keys(), values):
                    data[col_name] = value
                # Build record key from primary value
                primary_value = []
                for col_name in table.columns:
                    if table.primary_key and col_name in table.primary_key:
                        primary_value.append(data[col_name])
                primary_value = tuple(primary_value) if primary_value else None
                key = str(primary_value).encode() if primary_value else f"__tx_{id(op)}".encode()
                records[key] = data
            elif op[0] == "delete" and op[1] == table_name:
                _, _, where_clause = op
                keys_to_remove = []
                for key, data in records.items():
                    if self._evaluate_condition(deepcopy(where_clause), [table], data) == True:
                        keys_to_remove.append(key)
                for key in keys_to_remove:
                    del records[key]
            elif op[0] == "update" and op[1] == table_name:
                _, _, assignments, where_clause = op
                for key, data in list(records.items()):
                    if self._evaluate_condition(deepcopy(where_clause), [table], data) == True:
                        for col, val in assignments:
                            data[col] = val

        return records

    def _get_table_records_for_select(self, table_name: str, common_columns: set):
        """Return records formatted for SELECT (with prefixed common columns)."""
        records = self._get_table_records(table_name)
        result = []
        for key, data in records.items():
            record_data = {}
            for column_name, value in data.items():
                if column_name in common_columns:
                    prefixed = f"{table_name}.{column_name}"
                    record_data[prefixed] = value
                else:
                    record_data[column_name] = value
            result.append(record_data)
        return result

    def create_table(self, table_dict: dict):
        if self.tx_active:
            return self._create_table_in_transaction(table_dict)
        return self._do_create_table(table_dict)

    def _create_table_in_transaction(self, table_dict: dict):
        table_name = table_dict["table_name"]
        column_list = table_dict["column_list"]
        not_null_key_set = table_dict["not_null_key_set"]
        primary_key_list = table_dict["primary_key_list"]
        foreign_key_dict = table_dict["foreign_key_dict"]

        # Same validation as _do_create_table (except FK deferred to commit)
        if len(set([column_name for column_name, _ in column_list])) < len(column_list):
            raise DuplicateColumnDefError()
        columns = {column_name: column_type for column_name, column_type in column_list}

        for data_type in columns.values():
            if data_type.startswith("char"):
                if eval_char_max_len(data_type) < 1:
                    raise CharLengthError()

        if len(primary_key_list) > 1:
            raise DuplicatePrimaryKeyDefError()
        elif len(primary_key_list) == 0:
            primary_key = None
        else:
            primary_key = primary_key_list[0]
            for key in primary_key:
                if key not in columns:
                    raise NonExistingColumnDefError(key)
            not_null_key_set.update(primary_key)

        if foreign_key_dict:
            for foreign_key in foreign_key_dict:
                if foreign_key not in columns:
                    raise NonExistingColumnDefError(foreign_key)

        # Check against current transaction state
        if self._get_table_schema(table_name) is not None:
            raise TableExistenceError()

        # Validate foreign keys against committed tables only (tables created later
        # in the same transaction must be created before the referencing table)
        if foreign_key_dict:
            for foreign_key, (referenced_table_name, referenced_key) in foreign_key_dict.items():
                referenced_table = self._get_table_schema(referenced_table_name)
                if referenced_table is None:
                    raise ReferenceTableExistenceError()
                if referenced_key not in referenced_table:
                    raise ReferenceColumnExistenceError()
                if not referenced_table.check_reference_primary_key(referenced_key):
                    raise ReferenceNonPrimaryKeyError()
                foreign_key_type = columns[foreign_key]
                if not referenced_table.check_reference_type(foreign_key_type, referenced_key):
                    raise ReferenceTypeError()

        # Remove any pending drop for this table (re-create scenario)
        if table_name in self.tx_tables_dropped:
            self.tx_tables_dropped.discard(table_name)

        self.tx_tables_created[table_name] = table_dict
        self.tx_operations.append(("create_table", table_dict))
        return CreateTableSuccess(table_name)

    def _do_create_table(self, table_dict: dict):
        table_name = table_dict["table_name"]
        column_list = table_dict["column_list"]
        not_null_key_set = table_dict["not_null_key_set"]
        primary_key_list = table_dict["primary_key_list"]
        foreign_key_dict = table_dict["foreign_key_dict"]

        # Error within the table info
        if len(set([column_name for column_name, _ in column_list])) < len(column_list):
            raise DuplicateColumnDefError()
        columns = {column_name: column_type for column_name, column_type in column_list}
        
        for data_type in columns.values():
            if data_type.startswith("char"):
                if eval_char_max_len(data_type) < 1:  # hardcoding
                    raise CharLengthError()
        
        if len(primary_key_list) > 1:
            raise DuplicatePrimaryKeyDefError()
        elif len(primary_key_list) == 0:
            primary_key = None
        else:
            primary_key = primary_key_list[0]
            for key in primary_key:
                if key not in columns:
                    raise NonExistingColumnDefError(key)
            not_null_key_set.update(primary_key)
        
        if foreign_key_dict:
            for foreign_key in foreign_key_dict:
                if foreign_key not in columns:
                    raise NonExistingColumnDefError(foreign_key)   

        # Error within the database
        self.meta_db.open_db()
        
        table_key = self.meta_db.create_key_from_value(table_name)
        if self.meta_db.exists(table_key):
            raise TableExistenceError()
        
        if foreign_key_dict:
            for foreign_key, (referenced_table_name, referenced_key) in foreign_key_dict.items():
                referenced_table_key = self.meta_db.create_key_from_value(referenced_table_name)
                referenced_table = self.meta_db.get(referenced_table_key)
                if not referenced_table:
                    raise ReferenceTableExistenceError()
                if referenced_key not in referenced_table:
                    raise ReferenceColumnExistenceError()
                if not referenced_table.check_reference_primary_key(referenced_key):
                    raise ReferenceNonPrimaryKeyError()
                foreign_key_type = columns[foreign_key]
                if not referenced_table.check_reference_type(foreign_key_type, referenced_key):
                    raise ReferenceTypeError()
                referenced_table.add_reference(table_name)
                # update referenced table info
                self.meta_db.put(referenced_table_key, referenced_table)
        
        table = Table(
            table_name=table_name,
            columns=columns,
            not_null_keys=not_null_key_set,
            primary_key=primary_key,
            foreign_keys=foreign_key_dict
        )
        # add table info to meta db
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        # create table db
        table_db = DB(table_name)
        table_db.open_db()
        table_db.close_db()
        
        return CreateTableSuccess(table_name)

    def drop_table(self, table_name: str):
        if self.tx_active:
            return self._drop_table_in_transaction(table_name)
        return self._do_drop_table(table_name)

    def _drop_table_in_transaction(self, table_name: str):
        table = self._get_table_schema(table_name)
        if table is None:
            raise NoSuchTable()
        if table.has_reference():
            raise DropReferencedTableError(table_name)

        # If created in this transaction, just remove the create
        if table_name in self.tx_tables_created:
            del self.tx_tables_created[table_name]
            # Remove the create operation from the log
            self.tx_operations = [op for op in self.tx_operations if not (op[0] == "create_table" and op[1]["table_name"] == table_name)]
            return DropSuccess(table_name)

        self.tx_tables_dropped.add(table_name)
        self.tx_operations.append(("drop_table", table_name))
        return DropSuccess(table_name)

    def _do_drop_table(self, table_name: str):
        # remove table info
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        if table.has_reference():
            raise DropReferencedTableError(table_name)
        referencing_tables = table.get_referencing_tables()
        if referencing_tables:
            for referencing_table in referencing_tables:
                referencing_table_key = self.meta_db.create_key_from_value(referencing_table)
                referencing_table_db = self.meta_db.get(referencing_table_key)
                referencing_table_db.remove_reference(table_name)
                self.meta_db.put(referencing_table_key, referencing_table_db)
        self.meta_db.delete(table_key)
        
        # remove table records (delete all backend files, see DB.remove_files)
        DB(table_name).remove_files()
        
        # remove index files
        if hasattr(table, "indexes") and table.indexes:
            for idx_info in table.indexes.values():
                index_path = self.db_dir / idx_info["file"]
                for path in self.db_dir.glob(index_path.name + "*"):
                    path.unlink(missing_ok=True)
        
        self.meta_db.close_db()
        
        return DropSuccess(table_name)

    def explain_describe_desc(self, table_name: str):
        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()
        return table

    def show_tables(self):
        if self.tx_active:
            self.meta_db.open_db()
            all_tables = set(k.decode() for k in self.meta_db.keys())
            self.meta_db.close_db()
            all_tables.update(self.tx_tables_created.keys())
            all_tables.difference_update(self.tx_tables_dropped)
            output = "\n------------------------\n"
            for table_name in sorted(all_tables):
                output += table_name + "\n"
            output += "------------------------"
            return output

        self.meta_db.open_db()
        output = "\n------------------------\n"
        all_tables = self.meta_db.keys()
        for table_key in all_tables:
            output += table_key.decode() + "\n"
        output += "------------------------"
        self.meta_db.close_db()
        return output

    # --------------------------------------------------------------------- #
    #                            Index DDL                                  #
    # --------------------------------------------------------------------- #
    
    def create_index(self, table_name: str, index_name: str, column_name: str):
        if self.tx_active:
            return self._create_index_in_transaction(table_name, index_name, column_name)
        return self._do_create_index(table_name, index_name, column_name)

    def _create_index_in_transaction(self, table_name: str, index_name: str, column_name: str):
        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()
        if column_name not in table.columns:
            raise IndexColumnNotExist(column_name)
        if hasattr(table, "indexes") and index_name in table.indexes:
            raise DuplicateIndexError(index_name)

        # Buffer the operation (index file created on commit)
        if table_name not in self.tx_indexes_created:
            self.tx_indexes_created[table_name] = []
        self.tx_indexes_created[table_name].append((index_name, column_name))
        self.tx_operations.append(("create_index", table_name, index_name, column_name))
        return CreateIndexSuccess(index_name)

    def _do_create_index(self, table_name: str, index_name: str, column_name: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        if column_name not in table.columns:
            raise IndexColumnNotExist(column_name)
        if hasattr(table, "indexes") and index_name in table.indexes:
            raise DuplicateIndexError(index_name)
        
        # Create and populate B-Tree index
        index_file = f"{table_name}_{index_name}.idx"
        index_path = self.db_dir / index_file
        btree = BTreeIndex(str(index_path))
        btree.open()
        
        table_db = DB(table_name)
        table_db.open_db()
        cursor = table_db.create_cursor()
        pair = cursor.first()
        while pair:
            key, value = pair
            record = Record.deserialize(value)
            col_value = record.data.get(column_name)
            if col_value is not None:
                btree.insert(col_value, key)
            pair = cursor.next()
        table_db.discard_cursor(cursor)
        table_db.close_db()
        btree.close()
        
        # Update table metadata
        if not hasattr(table, "indexes"):
            table.indexes = {}
        table.indexes[index_name] = {"column": column_name, "file": index_file}
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        return CreateIndexSuccess(index_name)
    
    
    def drop_index(self, table_name: str, index_name: str):
        if self.tx_active:
            return self._drop_index_in_transaction(table_name, index_name)
        return self._do_drop_index(table_name, index_name)

    def _drop_index_in_transaction(self, table_name: str, index_name: str):
        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()
        if not hasattr(table, "indexes") or index_name not in table.indexes:
            raise NoSuchIndex(index_name)

        # If created in this transaction, just remove the create
        if table_name in self.tx_indexes_created:
            self.tx_indexes_created[table_name] = [
                (idx, col) for idx, col in self.tx_indexes_created[table_name] if idx != index_name
            ]
            self.tx_operations = [
                op for op in self.tx_operations
                if not (op[0] == "create_index" and op[1] == table_name and op[2] == index_name)
            ]
            return DropIndexSuccess(index_name)

        if table_name not in self.tx_indexes_dropped:
            self.tx_indexes_dropped[table_name] = []
        self.tx_indexes_dropped[table_name].append(index_name)
        self.tx_operations.append(("drop_index", table_name, index_name))
        return DropIndexSuccess(index_name)

    def _do_drop_index(self, table_name: str, index_name: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        if not hasattr(table, "indexes") or index_name not in table.indexes:
            raise NoSuchIndex(index_name)
        
        # Remove index files
        index_file = table.indexes[index_name]["file"]
        index_path = self.db_dir / index_file
        for path in self.db_dir.glob(index_path.name + "*"):
            path.unlink(missing_ok=True)
        
        # Update metadata
        del table.indexes[index_name]
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        return DropIndexSuccess(index_name)
    
    
    # --------------------------------------------------------------------- #
    #                            Index helpers                              #
    # --------------------------------------------------------------------- #
    
    def _find_index_predicate(self, table: Table, where_clause: dict):
        """Traverse a WHERE clause and return (column_name, op, value) if a
        comparison on an indexed column is found, else None.
        """
        condition = where_clause
        while condition and condition.get("op") is None:
            for key in ("boolean_terms", "boolean_factors", "boolean_test"):
                if key in condition:
                    condition = condition[key]
                    break
            else:
                break
        
        if not condition:
            return None
        
        op = condition.get("op")
        if op in comparison_op_map:
            left = condition.get("left_operand")
            right = condition.get("right_operand")
            if len(left) == 2 and len(right) == 1:
                tname, cname = left
                if (not tname or tname == table.table_name) and cname in table.columns:
                    for idx_info in getattr(table, "indexes", {}).values():
                        if idx_info["column"] == cname:
                            return cname, op, right[0]
            elif len(left) == 1 and len(right) == 2:
                tname, cname = right
                if (not tname or tname == table.table_name) and cname in table.columns:
                    for idx_info in getattr(table, "indexes", {}).values():
                        if idx_info["column"] == cname:
                            return cname, op, left[0]
        elif op == "and":
            for factor in condition.get("boolean_factors", []):
                result = self._find_index_predicate(table, factor)
                if result:
                    return result
        return None
    
    
    def _query_btree(self, table: Table, column_name: str, op: str, value):
        """Query the appropriate B-Tree index and return a set of record keys."""
        matching_index = None
        for idx_name, idx_info in table.indexes.items():
            if idx_info["column"] == column_name:
                matching_index = idx_name
                break
        if not matching_index:
            return None
        
        idx_info = table.indexes[matching_index]
        btree = BTreeIndex(str(self.db_dir / idx_info["file"]))
        btree.open()
        try:
            if op == "=":
                return btree.search(value)
            elif op == "<":
                return btree.range_search(high=value, high_inclusive=False)
            elif op == "<=":
                return btree.range_search(high=value, high_inclusive=True)
            elif op == ">":
                return btree.range_search(low=value, low_inclusive=False)
            elif op == ">=":
                return btree.range_search(low=value, low_inclusive=True)
        finally:
            btree.close()
        return None
    
    
    def _maintain_indexes_on_insert(self, table: Table, record_key: bytes, data: dict):
        """Insert the new record into every index defined on *table*."""
        if not hasattr(table, "indexes") or not table.indexes:
            return
        for idx_info in table.indexes.values():
            col_value = data.get(idx_info["column"])
            if col_value is not None:
                btree = BTreeIndex(str(self.db_dir / idx_info["file"]))
                btree.open()
                btree.insert(col_value, record_key)
                btree.close()
    
    
    def _maintain_indexes_on_delete(self, table: Table, record_key: bytes, record_data: dict):
        """Remove the deleted record from every index defined on *table*."""
        if not hasattr(table, "indexes") or not table.indexes:
            return
        for idx_info in table.indexes.values():
            col_value = record_data.get(idx_info["column"])
            if col_value is not None:
                btree = BTreeIndex(str(self.db_dir / idx_info["file"]))
                btree.open()
                btree.delete(col_value, record_key)
                btree.close()
    
    
    # --------------------------------------------------------------------- #
    #                              DML                                      #
    # --------------------------------------------------------------------- #
    
    def insert(self, table_dict: dict, value_list: list):
        if self.tx_active:
            return self._insert_in_transaction(table_dict, value_list)
        return self._do_insert(table_dict, value_list)

    def _insert_in_transaction(self, table_dict: dict, value_list: list):
        table_name = table_dict["table_name"]
        column_name_list = table_dict["column_name_list"]

        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()

        if column_name_list:
            if len(column_name_list) != len(value_list):
                raise InsertTypeMismatchError()
            for column_name in column_name_list:
                if column_name not in table:
                    raise InsertColumnExistenceError(column_name)

        if len(table.columns.keys()) != len(value_list):
            raise InsertTypeMismatchError()

        for column_name, value in zip(table.columns.keys(), value_list):
            if value is None and column_name in table.not_null_keys:
                raise InsertColumnNonNullableError(column_name)

        if not all([is_valid_type(data_type, value) for data_type, value in zip(table.columns.values(), value_list)]):
            raise InsertTypeMismatchError()

        # Build data dict for validation
        data = {}
        for col_name, value in zip(table.columns.keys(), value_list):
            data[col_name] = value

        # Check primary key uniqueness against transaction view
        primary_value = []
        for col_name in table.columns:
            if table.primary_key and col_name in table.primary_key:
                primary_value.append(data[col_name])
        primary_value = tuple(primary_value) if primary_value else None
        if primary_value:
            records = self._get_table_records(table_name)
            key = str(primary_value).encode()
            if key in records:
                raise InsertDuplicatePrimaryKeyError()

        # Check foreign key constraints against transaction view
        if table.foreign_keys:
            for col_name in table.columns:
                if col_name in table.foreign_keys:
                    referenced_table_name, referenced_column_name = table.foreign_keys[col_name]
                    value = data[col_name]
                    if value is not None:
                        ref_records = self._get_table_records(referenced_table_name)
                        found = False
                        for ref_data in ref_records.values():
                            if ref_data.get(referenced_column_name) == value:
                                found = True
                                break
                        if not found:
                            raise InsertReferentialIntegrityError()

        self.tx_operations.append(("insert", table_dict, value_list))
        return InsertResult()

    def _do_insert(self, table_dict: dict, value_list: list):
        table_name = table_dict["table_name"]
        column_name_list = table_dict["column_name_list"]
        
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        
        if column_name_list:
            if len(column_name_list) != len(value_list):
                raise InsertTypeMismatchError()
            for column_name in column_name_list:
                if column_name not in table:
                    raise InsertColumnExistenceError(column_name)
            
        if len(table.columns.keys()) != len(value_list):
            raise InsertTypeMismatchError()
        
        for column_name, value in zip(table.columns.keys(), value_list):
            if value is None and column_name in table.not_null_keys:
                raise InsertColumnNonNullableError(column_name)
        
        if not all([is_valid_type(data_type, value) for data_type, value in zip(table.columns.values(), value_list)]):
            raise InsertTypeMismatchError()
        
        data = {}
        primary_value = []
        referencing = dict()
        for (column_name, data_type), value in zip(table.columns.items(), value_list):
            if data_type.startswith("char") and value is not None:
                max_len = eval_char_max_len(data_type)
                value = value[:max_len]
            if table.primary_key and column_name in table.primary_key:  # may be composite primary key
                primary_value.append(value)
            if table.foreign_keys and column_name in table.foreign_keys:  # one foreign key per column
                referenced_table_name, referenced_column_name = table.foreign_keys[column_name]
                # get referenced table schema
                self.meta_db.open_db()
                referenced_table_key = self.meta_db.create_key_from_value(referenced_table_name)
                referenced_table = self.meta_db.get(referenced_table_key)
                self.meta_db.close_db()
                # get referenced record
                referenced_table_db = DB(referenced_table_name)
                referenced_table_db.open_db()
                referenced_key = referenced_table_db.create_key_from_value((value,))
                referenced_record = None
                if len(referenced_table.primary_key) == 1:
                    referenced_record = referenced_table_db.get(referenced_key)
                else:  # composite primary key
                    all_primary_values = referenced_table_db.keys()
                    for primary_value in all_primary_values:
                        if referenced_key.decode() in primary_value.decode():
                            referenced_record = referenced_table_db.get(primary_value)
                            break
                if referenced_record is None:
                    raise InsertReferentialIntegrityError()
                referencing[(referenced_table_name, referenced_column_name)] = {referenced_record.data[referenced_column_name]}
                assert referenced_record.data[referenced_column_name] == value
                referenced_record.add_to_referenced_by(table_name, column_name, value)
                referenced_table_db.put(referenced_key, referenced_record)
                referenced_table_db.close_db()
            data[column_name] = value
        primary_value = tuple(primary_value) if primary_value else None
        record = Record(table_name, data, primary_value, referencing)
        
        table_db = DB(table_name)
        table_db.open_db()
        record_key = table_db.create_key_from_value(primary_value) if primary_value else table_db.create_random_key()
        if table_db.exists(record_key):
            raise InsertDuplicatePrimaryKeyError()
        table_db.put(record_key, record)
        
        # Maintain indexes
        self._maintain_indexes_on_insert(table, record_key, data)
        
        table_db.close_db()
        
        return InsertResult()

    
    def delete(self, table_name: str, where_clause: str):
        if self.tx_active:
            return self._delete_in_transaction(table_name, where_clause)
        return self._do_delete(table_name, where_clause)

    def _delete_in_transaction(self, table_name: str, where_clause: str):
        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()

        records = self._get_table_records(table_name)

        success_cnt = 0
        fail_cnt = 0
        for key, record_data in records.items():
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record_data) if where_clause else True
            if satisfies == True:
                # Check referential integrity for committed records only
                if not key.startswith(b"__tx"):
                    table_db = DB(table_name)
                    table_db.open_db()
                    record = table_db.get(key)
                    table_db.close_db()
                    if record and list(record.referenced_by.values()):
                        fail_cnt += 1
                        continue
                success_cnt += 1

        if success_cnt > 0 or (where_clause is None and len(records) > 0):
            self.tx_operations.append(("delete", table_name, where_clause))

        return DeleteResult(success_cnt), DeleteReferentialIntegrityPassed(fail_cnt) if fail_cnt else None

    def _do_delete(self, table_name: str, where_clause: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        
        # ----------------------------------------------------------------- #
        #  Try to use an index for the WHERE clause (single-table only)    #
        # ----------------------------------------------------------------- #
        indexed_keys = None
        if where_clause and hasattr(table, "indexes") and table.indexes:
            predicate = self._find_index_predicate(table, where_clause)
            if predicate:
                cname, op, value = predicate
                indexed_keys = self._query_btree(table, cname, op, value)
        
        table_db = DB(table_name)
        table_db.open_db()
        
        if indexed_keys is not None:
            success_cnt = 0
            fail_cnt = 0
            for key in indexed_keys:
                record = table_db.get(key)
                if record is None:
                    continue
                satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record.data) if where_clause else True
                if satisfies == True:
                    if list(record.referenced_by.values()):
                        fail_cnt += 1
                    else:
                        if record.referencing:
                            for (referenced_table_name, referenced_column_name), referenced_value_set in record.referencing.items():
                                for referenced_value in referenced_value_set:
                                    referenced_table_db = DB(referenced_table_name)
                                    referenced_table_db.open_db()
                                    inner_cursor = referenced_table_db.create_cursor()
                                    key_value_pair = inner_cursor.first()
                                    while key_value_pair:
                                        k2, v2 = key_value_pair
                                        referenced_record = Record.deserialize(v2)
                                        for column in table.columns:
                                            if ((table_name, column) in referenced_record.referenced_by and 
                                                referenced_value in referenced_record.referenced_by[(table_name, column)]):
                                                referenced_record.remove_referenced_by(table_name, column, referenced_value)
                                                referenced_table_db.put(k2, referenced_record)
                                        key_value_pair = inner_cursor.next()
                                    referenced_table_db.discard_cursor(inner_cursor)
                                    referenced_table_db.close_db()
                        
                        # Maintain indexes before physical delete
                        self._maintain_indexes_on_delete(table, key, record.data)
                        
                        table_db.delete(key)
                        success_cnt += 1
            table_db.close_db()
            return DeleteResult(success_cnt), DeleteReferentialIntegrityPassed(fail_cnt) if fail_cnt else None
        
        # ----------------------------------------------------------------- #
        #  Fall back to full table scan                                     #
        # ----------------------------------------------------------------- #
        outer_cursor = table_db.create_cursor()
        
        success_cnt = 0
        fail_cnt = 0
        key_value_pair = outer_cursor.first()
        while key_value_pair:
            key, value = key_value_pair
            record = Record.deserialize(value)
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record.data) if where_clause else True
            if satisfies == True:
                if list(record.referenced_by.values()):
                    fail_cnt += 1
                else:
                    if record.referencing:
                        for (referenced_table_name, referenced_column_name), referenced_value_set in record.referencing.items():
                            for referenced_value in referenced_value_set:
                                referenced_table_db = DB(referenced_table_name)
                                referenced_table_db.open_db()
                                inner_cursor = referenced_table_db.create_cursor()
                                key_value_pair = inner_cursor.first()
                                while key_value_pair:
                                    k2, v2 = key_value_pair
                                    referenced_record = Record.deserialize(v2)
                                    for column in table.columns:
                                        if ((table_name, column) in referenced_record.referenced_by and 
                                            referenced_value in referenced_record.referenced_by[(table_name, column)]):
                                            referenced_record.remove_referenced_by(table_name, column, referenced_value)
                                            referenced_table_db.put(k2, referenced_record)
                                    key_value_pair = inner_cursor.next()
                                referenced_table_db.discard_cursor(inner_cursor)
                                referenced_table_db.close_db()
                    
                    # Maintain indexes before physical delete
                    self._maintain_indexes_on_delete(table, key, record.data)
                    
                    table_db.delete_by_cursor(outer_cursor)
                    success_cnt += 1
            key_value_pair = outer_cursor.next()
            
        table_db.discard_cursor(outer_cursor)
        table_db.close_db()
        
        return DeleteResult(success_cnt), DeleteReferentialIntegrityPassed(fail_cnt) if fail_cnt else None
    
    
    def update(self, table_name: str, assignments: list, where_clause: dict):
        if self.tx_active:
            return self._update_in_transaction(table_name, assignments, where_clause)
        return self._do_update(table_name, assignments, where_clause)

    def _update_in_transaction(self, table_name: str, assignments: list, where_clause: dict):
        table = self._get_table_schema(table_name)
        if not table:
            raise NoSuchTable()

        for column_name, value in assignments:
            if column_name not in table.columns:
                raise UpdateColumnExistenceError(column_name)
            if value is None and column_name in table.not_null_keys:
                raise UpdateColumnNonNullableError(column_name)
            if not is_valid_type(table.columns[column_name], value):
                raise UpdateTypeMismatchError()

        records = self._get_table_records(table_name)

        success_cnt = 0
        fail_cnt = 0
        for key, record_data in records.items():
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record_data) if where_clause else True
            if satisfies == True:
                pk_updated = False
                if table.primary_key:
                    for col, _ in assignments:
                        if col in table.primary_key:
                            pk_updated = True
                            break

                if pk_updated:
                    if not key.startswith(b"__tx"):
                        table_db = DB(table_name)
                        table_db.open_db()
                        record = table_db.get(key)
                        table_db.close_db()
                        if record and list(record.referenced_by.values()):
                            fail_cnt += 1
                            continue

                    new_data = dict(record_data)
                    for col, val in assignments:
                        new_data[col] = val

                    new_primary_value = []
                    for col_name in table.columns:
                        if table.primary_key and col_name in table.primary_key:
                            new_primary_value.append(new_data[col_name])
                    new_primary_value = tuple(new_primary_value) if new_primary_value else None
                    new_key = str(new_primary_value).encode() if new_primary_value else key

                    if new_key in records and new_key != key:
                        fail_cnt += 1
                        continue
                else:
                    new_data = dict(record_data)
                    for col, val in assignments:
                        new_data[col] = val

                # Check FK constraints
                fk_ok = True
                if table.foreign_keys:
                    for col_name in [c for c, _ in assignments]:
                        if col_name in table.foreign_keys:
                            new_value = new_data[col_name]
                            if new_value is not None:
                                ref_table_name, ref_col_name = table.foreign_keys[col_name]
                                ref_records = self._get_table_records(ref_table_name)
                                found = False
                                for ref_data in ref_records.values():
                                    if ref_data.get(ref_col_name) == new_value:
                                        found = True
                                        break
                                if not found:
                                    fk_ok = False
                                    break

                if not fk_ok:
                    fail_cnt += 1
                    continue

                success_cnt += 1

        if success_cnt > 0 or (where_clause is None and len(records) > 0):
            self.tx_operations.append(("update", table_name, assignments, where_clause))

        return UpdateResult(success_cnt), UpdateReferentialIntegrityPassed(fail_cnt) if fail_cnt else None

    def _do_update(self, table_name: str, assignments: list, where_clause: dict):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()

        for column_name, value in assignments:
            if column_name not in table.columns:
                raise UpdateColumnExistenceError(column_name)
            if value is None and column_name in table.not_null_keys:
                raise UpdateColumnNonNullableError(column_name)
            if not is_valid_type(table.columns[column_name], value):
                raise UpdateTypeMismatchError()

        table_db = DB(table_name)
        table_db.open_db()
        cursor = table_db.create_cursor()

        success_cnt = 0
        fail_cnt = 0

        key_value_pair = cursor.first()
        while key_value_pair:
            key, value = key_value_pair
            record = Record.deserialize(value)
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record.data) if where_clause else True
            if satisfies == True:
                new_data = dict(record.data)
                for col, val in assignments:
                    new_data[col] = val

                pk_updated = False
                if table.primary_key:
                    for col, _ in assignments:
                        if col in table.primary_key:
                            pk_updated = True
                            break

                if pk_updated:
                    if list(record.referenced_by.values()):
                        fail_cnt += 1
                        key_value_pair = cursor.next()
                        continue

                    new_primary_value = []
                    for col_name in table.columns:
                        if table.primary_key and col_name in table.primary_key:
                            new_primary_value.append(new_data[col_name])
                    new_primary_value = tuple(new_primary_value) if new_primary_value else None
                    new_key = table_db.create_key_from_value(new_primary_value) if new_primary_value else table_db.create_random_key()

                    if new_key != key and table_db.exists(new_key):
                        fail_cnt += 1
                        key_value_pair = cursor.next()
                        continue
                else:
                    new_key = key
                    new_primary_value = record.primary_value

                # Check FK constraints
                fk_ok = True
                if table.foreign_keys:
                    for col_name, new_value in assignments:
                        if col_name in table.foreign_keys and new_value is not None:
                            ref_table_name, ref_col_name = table.foreign_keys[col_name]
                            self.meta_db.open_db()
                            ref_table_key = self.meta_db.create_key_from_value(ref_table_name)
                            ref_table_schema = self.meta_db.get(ref_table_key)
                            self.meta_db.close_db()

                            ref_table_db = DB(ref_table_name)
                            ref_table_db.open_db()
                            ref_cursor = ref_table_db.create_cursor()
                            found = False
                            ref_pair = ref_cursor.first()
                            while ref_pair:
                                ref_key, ref_val = ref_pair
                                ref_record = Record.deserialize(ref_val)
                                if ref_record.data.get(ref_col_name) == new_value:
                                    found = True
                                    break
                                ref_pair = ref_cursor.next()
                            ref_table_db.discard_cursor(ref_cursor)
                            ref_table_db.close_db()

                            if not found:
                                fk_ok = False
                                break

                if not fk_ok:
                    fail_cnt += 1
                    key_value_pair = cursor.next()
                    continue

                # Remove old index entries
                self._maintain_indexes_on_delete(table, key, record.data)

                # Update referencing info for FK changes
                new_referencing = dict(record.referencing)
                for col_name, new_value in assignments:
                    if col_name in table.foreign_keys:
                        ref_table_name, ref_col_name = table.foreign_keys[col_name]
                        ref_pair = (ref_table_name, ref_col_name)

                        # Remove old referencing
                        old_value = record.data.get(col_name)
                        if old_value is not None:
                            if ref_pair in new_referencing and old_value in new_referencing.get(ref_pair, set()):
                                new_referencing[ref_pair].remove(old_value)
                                if not new_referencing[ref_pair]:
                                    del new_referencing[ref_pair]

                            # Update old referenced record's referenced_by
                            ref_table_db = DB(ref_table_name)
                            ref_table_db.open_db()
                            ref_cursor = ref_table_db.create_cursor()
                            ref_pair2 = ref_cursor.first()
                            while ref_pair2:
                                ref_key2, ref_val2 = ref_pair2
                                ref_record2 = Record.deserialize(ref_val2)
                                if ref_record2.data.get(ref_col_name) == old_value:
                                    ref_record2.remove_referenced_by(table_name, col_name, old_value)
                                    ref_table_db.put(ref_key2, ref_record2)
                                    break
                                ref_pair2 = ref_cursor.next()
                            ref_table_db.discard_cursor(ref_cursor)
                            ref_table_db.close_db()

                        # Add new referencing
                        if new_value is not None:
                            if ref_pair not in new_referencing:
                                new_referencing[ref_pair] = set()
                            new_referencing[ref_pair].add(new_value)

                            ref_table_db = DB(ref_table_name)
                            ref_table_db.open_db()
                            ref_cursor = ref_table_db.create_cursor()
                            ref_pair2 = ref_cursor.first()
                            while ref_pair2:
                                ref_key2, ref_val2 = ref_pair2
                                ref_record2 = Record.deserialize(ref_val2)
                                if ref_record2.data.get(ref_col_name) == new_value:
                                    ref_record2.add_to_referenced_by(table_name, col_name, new_value)
                                    ref_table_db.put(ref_key2, ref_record2)
                                    break
                                ref_pair2 = ref_cursor.next()
                            ref_table_db.discard_cursor(ref_cursor)
                            ref_table_db.close_db()

                new_record = Record(table_name, new_data, new_primary_value, new_referencing, dict(record.referenced_by))

                if pk_updated and new_key != key:
                    table_db.delete(key)

                table_db.put(new_key, new_record)

                # Add new index entries
                self._maintain_indexes_on_insert(table, new_key, new_data)

                success_cnt += 1

            key_value_pair = cursor.next()

        table_db.discard_cursor(cursor)
        table_db.close_db()

        return UpdateResult(success_cnt), UpdateReferentialIntegrityPassed(fail_cnt) if fail_cnt else None
    
    
    def _evaluate_condition(self, condition, table_list: List[Table], record: dict):
        def get_record_value(operand):
            table_name, column_name = operand
            if table_name and not any([table_name == table.table_name for table in table_list]):
                raise WhereTableNotSpecified()
            found_tables = [table for table in table_list if column_name in table]
            if len(found_tables) < 1:
                raise WhereColumnNotExist()
            elif len(found_tables) > 1:
                if not table_name:  # column name is ambiguous
                    raise WhereAmbiguousReference()
                table = next(table for table in found_tables if table_name == table.table_name)
            else:
                table = found_tables[0]
            if table_name and table_name != table.table_name:
                raise WhereColumnNotExist()
            if table_name:
                prefixed_column_name = f"{table_name}.{column_name}"
                if prefixed_column_name in record:
                    return record[prefixed_column_name]
            return record[column_name]
        
        def determine_operand_value(operand):
            if operand is None:
                value = operand
            elif len(operand) == 1:  # comparable_value
                value = operand[0]
            else:  # table_name, column_name
                value = get_record_value(operand)
            return value
            
        op = condition["op"]
        if op in comparison_op_map | null_op_map:
            op, left_operand, right_operand = map(condition.get, ["op", "left_operand", "right_operand"])
            left_value = determine_operand_value(left_operand)
            right_value = determine_operand_value(right_operand)
            
            if op in comparison_op_map and is_comparable(left_value, right_value) == False:
                raise WhereIncomparableError()
            
            if op in comparison_op_map:
                if left_value is None or right_value is None:
                    output = UNKNOWN
                else:
                    output = comparison_op_map[op](left_value, right_value)
            else:
                output = null_op_map[op](left_value, right_value)
            return output
            
        elif op == "not":
            boolean_test = condition["boolean_test"]
            return not_(self._evaluate_condition(boolean_test, table_list, record))
        
        elif op == "and":
            boolean_factors = condition["boolean_factors"]
            return and_(*[self._evaluate_condition(boolean_factor, table_list, record) for boolean_factor in boolean_factors])
        
        elif op == "or":
            boolean_terms = condition["boolean_terms"]
            return or_(*[self._evaluate_condition(boolean_term, table_list, record) for boolean_term in boolean_terms])
        
        else:  # None
            _, remaining_condition = condition.popitem()  # "boolean_terms", "boolean_factors", "boolean_test"
            if remaining_condition is not None:
                return self._evaluate_condition(remaining_condition, table_list, record)
    
    
    def select(self, tables: list, select_columns: list, where_clause: dict):
        if self.tx_active:
            return self._select_in_transaction(tables, select_columns, where_clause)

        table_list = []
        self.meta_db.open_db()
        for table_name in tables:
            table_key = self.meta_db.create_key_from_value(table_name)
            table = self.meta_db.get(table_key)
            if not table:
                raise SelectTableExistenceError(table_name)
            table_list.append(table)
        self.meta_db.close_db()
        
        final_columns = []
        if select_columns:
            for table_name, column_name in select_columns:
                found_tables = [table for table in table_list if column_name in table]
                if len(found_tables) < 1:
                    raise SelectColumnResolveError(column_name)
                elif len(found_tables) > 1:
                    if not table_name:
                        raise SelectColumnResolveError(column_name)
                    found_table = next(table for table in found_tables if table_name == table.table_name)
                else:
                    found_table = found_tables[0]       
                if table_name and table_name != found_table.table_name:
                    raise SelectColumnResolveError(column_name)
                final_column = f"{found_table.table_name}.{column_name}" if table_name else column_name
                final_columns.append(final_column)
        
        all_columns = []
        for table_schema in table_list:
            all_columns.extend(list(table_schema.columns.keys()))
        counter = Counter(all_columns)
        common_columns = set([column for column, count in counter.items() if count > 1])
        
        # ----------------------------------------------------------------- #
        #  Single-table index fast path                                    #
        # ----------------------------------------------------------------- #
        if len(tables) == 1:
            table_name = tables[0]
            table = table_list[0]
            indexed_records = None
            if where_clause and hasattr(table, "indexes") and table.indexes:
                predicate = self._find_index_predicate(table, where_clause)
                if predicate:
                    cname, op, value = predicate
                    record_keys = self._query_btree(table, cname, op, value)
                    if record_keys is not None:
                        table_db = DB(table_name)
                        table_db.open_db()
                        indexed_records = []
                        for key in record_keys:
                            record = table_db.get(key)
                            if record:
                                indexed_records.append(record.data)
                        table_db.close_db()
                        # Apply full WHERE filter to be safe
                        if where_clause and indexed_records:
                            filtered = []
                            for rec in indexed_records:
                                satisfies = self._evaluate_condition(deepcopy(where_clause), [table], rec)
                                if satisfies == True:
                                    filtered.append(rec)
                            indexed_records = filtered
            
            if indexed_records is not None:
                if select_columns:
                    final_records = []
                    for record in indexed_records:
                        final_record = {}
                        for tname, cname in select_columns:
                            if tname:
                                pcol = f"{tname}.{cname}"
                                try:
                                    final_record[pcol] = record[pcol]
                                except KeyError:
                                    final_record[pcol] = record[cname]
                            else:
                                final_record[cname] = record[cname]
                        final_records.append(final_record)
                else:
                    final_records = indexed_records
                    final_columns = []
                    for table_schema in table_list:
                        for column in table_schema.columns:
                            if column in common_columns:
                                final_columns.append(f"{table_schema.table_name}.{column}")
                            else:
                                final_columns.append(column)
                
                return self._format_select_output(final_records, final_columns)
        
        # ----------------------------------------------------------------- #
        #  Multi-table scan (or no usable index)                          #
        # ----------------------------------------------------------------- #
                    
        all_records_with_table = {}
        for table_name in tables:
            all_records_with_table[table_name] = []
            table_db = DB(table_name)
            table_db.open_db()
            cursor = table_db.create_cursor()
            key_value_pair = cursor.first()
            while key_value_pair:
                key, value = key_value_pair
                record = Record.deserialize(value)
                record_data = {}
                for column_name, value in record.data.items():
                    if column_name in common_columns:
                        prefixed_column_name = f"{table_name}.{column_name}"
                        record_data[prefixed_column_name] = value
                    else:
                        record_data[column_name] = value
                all_records_with_table[table_name].append(record_data)
                key_value_pair = cursor.next()
            table_db.discard_cursor(cursor)
            table_db.close_db()
        
        cartesian_product = itertools.product(*all_records_with_table.values())
        records_product = [{k: v for record in combination_tuple for k, v in record.items()} for combination_tuple in cartesian_product]
        
        if where_clause:
            filtered_records = []
            for record in records_product:
                satisfies = self._evaluate_condition(deepcopy(where_clause), table_list, record)  # otherwise the original where is modified
                if satisfies == True:
                    filtered_records.append(record)
        else:
            filtered_records = records_product  # list of dict[column_name, value]
            
        if select_columns:  # final output has headers by the specification of select_columns
            final_records = []
            for record in filtered_records:
                final_record = {}
                for table_name, column_name in select_columns:
                    value = None
                    if table_name:
                        prefixed_column_name = f"{table_name}.{column_name}"
                        try:
                            final_record[prefixed_column_name] = record[prefixed_column_name]
                        except KeyError:
                            final_record[prefixed_column_name] = record[column_name]
                    else:
                        final_record[column_name] = record[column_name]
                final_records.append(final_record)
        else:
            final_records = filtered_records
            final_columns = []
            for table_schema in table_list:
                for column in table_schema.columns:
                    if column in common_columns:
                        final_columns.append(f"{table_schema.table_name}.{column}")
                    else:
                        final_columns.append(column)
            
        headers = final_records[0].keys() if final_records else final_columns
        
        return self._format_select_output(final_records, headers)

    def _select_in_transaction(self, tables: list, select_columns: list, where_clause: dict):
        """SELECT that merges committed data with uncommitted transaction changes."""
        table_list = []
        for table_name in tables:
            table = self._get_table_schema(table_name)
            if not table:
                raise SelectTableExistenceError(table_name)
            table_list.append(table)

        final_columns = []
        if select_columns:
            for table_name, column_name in select_columns:
                found_tables = [table for table in table_list if column_name in table]
                if len(found_tables) < 1:
                    raise SelectColumnResolveError(column_name)
                elif len(found_tables) > 1:
                    if not table_name:
                        raise SelectColumnResolveError(column_name)
                    found_table = next(table for table in found_tables if table_name == table.table_name)
                else:
                    found_table = found_tables[0]
                if table_name and table_name != found_table.table_name:
                    raise SelectColumnResolveError(column_name)
                final_column = f"{found_table.table_name}.{column_name}" if table_name else column_name
                final_columns.append(final_column)

        all_columns = []
        for table_schema in table_list:
            all_columns.extend(list(table_schema.columns.keys()))
        counter = Counter(all_columns)
        common_columns = set([column for column, count in counter.items() if count > 1])

        # Get merged records for all tables (no index fast path in transactions)
        all_records_with_table = {}
        for table_name in tables:
            all_records_with_table[table_name] = self._get_table_records_for_select(table_name, common_columns)

        cartesian_product = itertools.product(*all_records_with_table.values())
        records_product = [{k: v for record in combination_tuple for k, v in record.items()} for combination_tuple in cartesian_product]

        if where_clause:
            filtered_records = []
            for record in records_product:
                satisfies = self._evaluate_condition(deepcopy(where_clause), table_list, record)
                if satisfies == True:
                    filtered_records.append(record)
        else:
            filtered_records = records_product

        if select_columns:
            final_records = []
            for record in filtered_records:
                final_record = {}
                for table_name, column_name in select_columns:
                    if table_name:
                        prefixed_column_name = f"{table_name}.{column_name}"
                        try:
                            final_record[prefixed_column_name] = record[prefixed_column_name]
                        except KeyError:
                            final_record[prefixed_column_name] = record[column_name]
                    else:
                        final_record[column_name] = record[column_name]
                final_records.append(final_record)
        else:
            final_records = filtered_records
            final_columns = []
            for table_schema in table_list:
                for column in table_schema.columns:
                    if column in common_columns:
                        final_columns.append(f"{table_schema.table_name}.{column}")
                    else:
                        final_columns.append(column)

        headers = final_records[0].keys() if final_records else final_columns
        return self._format_select_output(final_records, headers)
        
    
    def _format_select_output(self, records: List[Dict], headers: List[str]):
        def create_separator(column_widths):
            return '+-' + '-+-'.join('-' * width for width in column_widths) + '-+'
        
        for record in records:
            for k, v in record.items():
                if v is None:
                    record[k] = "null"
        
        column_widths = [len(header) for header in headers]
        for record in records:
            for i, value in enumerate(record.values()):
                column_widths[i] = max(column_widths[i], len(str(value)))
        
        output = '\n'
        output += create_separator(column_widths) + '\n'
        output += '| ' + ' | '.join(header.upper().ljust(width) for header, width in zip(headers, column_widths)) + ' |\n'
        output += create_separator(column_widths) + '\n'
        
        for record in records:
            output += '| ' + ' | '.join(str(value).ljust(width) for value, width in zip(record.values(), column_widths)) + ' |\n'
        output += create_separator(column_widths)
        
        return output
