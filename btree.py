import pickle
import dbm
from pathlib import Path


class BTreeNode:
    """A node in the B-Tree.
    
    Keys and values are parallel lists.  Each key maps to a *set* of record keys
    so that non-unique single-column indexes are supported.
    """
    __slots__ = ("leaf", "keys", "values", "children")

    def __init__(self, leaf: bool = False):
        self.leaf = leaf
        self.keys = []        # sorted list of column values
        self.values = []      # list of sets of record keys (bytes)
        self.children = []    # child node ids; len == len(keys) + 1 when not leaf


class BTreeIndex:
    """A simple on-disk B-Tree index backed by a dbm file.

    Each node is stored as a pickled dict under its numeric id.
    Metadata (root_id, degree, next_node_id) lives under the key ``b'__meta__'``.
    """

    def __init__(self, db_path: str, degree: int = 10):
        self.db_path = Path(db_path)
        self.db = None
        self.degree = degree
        self.root_id = None
        self.next_node_id = 0

    # ------------------------------ Lifecycle ------------------------------- #

    def open(self):
        self.db = dbm.open(str(self.db_path), "c")
        if b"__meta__" in self.db:
            meta = pickle.loads(self.db[b"__meta__"])
            self.root_id = meta["root_id"]
            self.degree = meta["degree"]
            self.next_node_id = meta["next_node_id"]
        else:
            self.root_id = self._allocate_node_id()
            root = BTreeNode(leaf=True)
            self._write_node(self.root_id, root)
            self._write_meta()

    def close(self):
        if self.db:
            self.db.close()
            self.db = None

    def remove_files(self):
        """Delete every on-disk file backing this index."""
        for path in self.db_path.parent.glob(self.db_path.name + "*"):
            path.unlink(missing_ok=True)

    # --------------------------- Persistence ------------------------------- #

    def _allocate_node_id(self) -> int:
        nid = self.next_node_id
        self.next_node_id += 1
        return nid

    def _write_node(self, node_id: int, node: BTreeNode):
        self.db[str(node_id).encode()] = pickle.dumps({
            "leaf": node.leaf,
            "keys": node.keys,
            "values": node.values,
            "children": node.children,
        })

    def _read_node(self, node_id: int) -> BTreeNode:
        data = pickle.loads(self.db[str(node_id).encode()])
        node = BTreeNode(leaf=data["leaf"])
        node.keys = data["keys"]
        node.values = data["values"]
        node.children = data["children"]
        return node

    def _write_meta(self):
        self.db[b"__meta__"] = pickle.dumps({
            "root_id": self.root_id,
            "degree": self.degree,
            "next_node_id": self.next_node_id,
        })

    # -------------------------- Core operations --------------------------- #

    def insert(self, key, record_key: bytes):
        """Insert *record_key* into the set stored at *key*."""
        root = self._read_node(self.root_id)
        if len(root.keys) == 2 * self.degree - 1:
            old_root_id = self.root_id
            self.root_id = self._allocate_node_id()
            new_root = BTreeNode(leaf=False)
            new_root.children.append(old_root_id)
            self._write_node(self.root_id, new_root)
            self._split_child(self.root_id, 0, old_root_id)

        self._insert_non_full(self.root_id, key, record_key)
        self._write_meta()

    def _split_child(self, parent_id: int, i: int, child_id: int):
        degree = self.degree
        parent = self._read_node(parent_id)
        child = self._read_node(child_id)
        new_child_id = self._allocate_node_id()
        new_child = BTreeNode(leaf=child.leaf)

        mid = degree - 1
        median_key = child.keys[mid]
        median_value = child.values[mid]

        new_child.keys = child.keys[mid + 1:]
        new_child.values = child.values[mid + 1:]
        if not child.leaf:
            new_child.children = child.children[mid + 1:]
            child.children = child.children[:mid + 1]

        child.keys = child.keys[:mid]
        child.values = child.values[:mid]

        parent.keys.insert(i, median_key)
        parent.values.insert(i, median_value)
        parent.children.insert(i + 1, new_child_id)

        self._write_node(child_id, child)
        self._write_node(new_child_id, new_child)
        self._write_node(parent_id, parent)

    def _insert_non_full(self, node_id: int, key, record_key: bytes):
        node = self._read_node(node_id)
        i = len(node.keys) - 1

        if node.leaf:
            while i >= 0 and key < node.keys[i]:
                i -= 1
            if i >= 0 and key == node.keys[i]:
                node.values[i].add(record_key)
            else:
                node.keys.insert(i + 1, key)
                node.values.insert(i + 1, {record_key})
            self._write_node(node_id, node)
        else:
            while i >= 0 and key < node.keys[i]:
                i -= 1
            if i >= 0 and key == node.keys[i]:
                node.values[i].add(record_key)
                self._write_node(node_id, node)
                return
            i += 1
            child_id = node.children[i]
            child = self._read_node(child_id)
            if len(child.keys) == 2 * self.degree - 1:
                self._split_child(node_id, i, child_id)
                node = self._read_node(node_id)
                if key > node.keys[i]:
                    i += 1
            self._insert_non_full(node.children[i], key, record_key)

    def search(self, key):
        """Return the set of record keys exactly matching *key*."""
        return self._search_node(self.root_id, key)

    def _search_node(self, node_id: int, key):
        node = self._read_node(node_id)
        i = 0
        while i < len(node.keys) and key > node.keys[i]:
            i += 1
        if i < len(node.keys) and key == node.keys[i]:
            return set(node.values[i])
        if node.leaf:
            return set()
        return self._search_node(node.children[i], key)

    def range_search(self, low=None, high=None, low_inclusive: bool = True, high_inclusive: bool = True):
        """Return the set of record keys whose column value falls in [*low*, *high*].

        Bounds that are ``None`` are treated as unbounded.
        """
        result = set()
        self._inorder_range(self.root_id, low, high, low_inclusive, high_inclusive, result)
        return result

    def _inorder_range(self, node_id: int, low, high, low_inclusive: bool, high_inclusive: bool, result: set):
        node = self._read_node(node_id)
        n = len(node.keys)
        for i in range(n):
            if not node.leaf:
                self._inorder_range(node.children[i], low, high, low_inclusive, high_inclusive, result)
            key = node.keys[i]
            in_range = True
            if low is not None:
                if low_inclusive:
                    in_range = in_range and (low <= key)
                else:
                    in_range = in_range and (low < key)
            if high is not None:
                if high_inclusive:
                    in_range = in_range and (key <= high)
                else:
                    in_range = in_range and (key < high)
            if in_range:
                result.update(node.values[i])
        if not node.leaf:
            self._inorder_range(node.children[n], low, high, low_inclusive, high_inclusive, result)

    def delete(self, key, record_key: bytes):
        """Remove *record_key* from the set stored at *key*.

        This is a **lazy** delete – the key itself is never removed from the
        tree node, which keeps the implementation simple while still correct.
        """
        if self.root_id is None:
            return
        self._delete_from_node(self.root_id, key, record_key)

    def _delete_from_node(self, node_id: int, key, record_key: bytes):
        node = self._read_node(node_id)
        for i, k in enumerate(node.keys):
            if k == key:
                if record_key in node.values[i]:
                    node.values[i].discard(record_key)
                    self._write_node(node_id, node)
                return
        if not node.leaf:
            for child_id in node.children:
                self._delete_from_node(child_id, key, record_key)
