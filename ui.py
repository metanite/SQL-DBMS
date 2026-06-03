import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import re
import os
import sys

# Ensure project modules are importable regardless of cwd
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lark import Lark
from dbms import DBMS
from messages import *
from sql_transformer import SQLTransformer

# --------------------------------------------------------------------------- #
# SQL Keywords for basic syntax highlighting
# --------------------------------------------------------------------------- #
SQL_KEYWORDS = {
    "select", "from", "where", "insert", "into", "values", "delete", "update",
    "set", "create", "table", "drop", "show", "tables", "explain", "describe",
    "desc", "primary", "key", "foreign", "references", "not", "null", "and",
    "or", "int", "char", "date", "unique", "default", "constraint", "index",
    "on", "as", "join", "inner", "left", "right", "outer", "cross", "natural",
    "group", "by", "order", "having", "limit", "offset", "union", "all",
    "distinct", "like", "in", "between", "is", "exists", "case", "when", "then",
    "else", "end", "if", "true", "false", "unknown",
}

# Colors for highlighting
KEYWORD_COLOR = "#00008B"  # dark blue
STRING_COLOR = "#006400"   # dark green
NUMBER_COLOR = "#8B0000"   # dark red
COMMENT_COLOR = "#808080"  # gray


class SQLText(scrolledtext.ScrolledText):
    """ScrolledText with basic SQL syntax highlighting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tag_configure("keyword", foreground=KEYWORD_COLOR, font=("Consolas", 11, "bold"))
        self.tag_configure("string", foreground=STRING_COLOR)
        self.tag_configure("number", foreground=NUMBER_COLOR)
        self.tag_configure("comment", foreground=COMMENT_COLOR)

        # Debounce timer id
        self._highlight_after_id = None
        self.bind("<KeyRelease>", self._on_key_release)
        self.bind("<Return>", self._on_return)
        self.bind("<Tab>", self._on_tab)

    def _on_key_release(self, event):
        # Debounce highlighting so it doesn't lag on fast typing
        if self._highlight_after_id is not None:
            self.after_cancel(self._highlight_after_id)
        self._highlight_after_id = self.after(150, self._highlight)

    def _highlight(self):
        # Remove existing tags
        for tag in ("keyword", "string", "number", "comment"):
            self.tag_remove(tag, "1.0", tk.END)

        text = self.get("1.0", tk.END)
        lines = text.split("\n")

        for line_no, line in enumerate(lines, start=1):
            # Comments
            for m in re.finditer(r"(--[^\n]*|#.*)", line):
                start_idx = f"{line_no}.{m.start()}"
                end_idx = f"{line_no}.{m.end()}"
                self.tag_add("comment", start_idx, end_idx)

            # Remove comment portion from further tokenization for this line
            code_line = re.split(r"(--[^\n]*|#.*)", line)[0]

            # Strings (single-quoted)
            for m in re.finditer(r"'([^']*)'", code_line):
                start_idx = f"{line_no}.{m.start()}"
                end_idx = f"{line_no}.{m.end()}"
                self.tag_add("string", start_idx, end_idx)

            # Numbers
            for m in re.finditer(r"\b\d+\b", code_line):
                start_idx = f"{line_no}.{m.start()}"
                end_idx = f"{line_no}.{m.end()}"
                self.tag_add("number", start_idx, end_idx)

            # Keywords (case-insensitive match, preserve original case for tag position)
            for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", code_line):
                word = m.group().lower()
                if word in SQL_KEYWORDS:
                    start_idx = f"{line_no}.{m.start()}"
                    end_idx = f"{line_no}.{m.end()}"
                    self.tag_add("keyword", start_idx, end_idx)

    def _on_return(self, event):
        # Auto-indent: copy leading whitespace from current line
        index = self.index(tk.INSERT)
        line, col = map(int, index.split("."))
        line_text = self.get(f"{line}.0", f"{line}.end")
        leading = re.match(r"^(\s*)", line_text).group(1)
        self.insert(tk.INSERT, "\n" + leading)
        return "break"

    def _on_tab(self, event):
        self.insert(tk.INSERT, "    ")
        return "break"


class ResultTreeview(ttk.Treeview):
    """Treeview wrapper for displaying tabular SQL results."""

    def __init__(self, parent, **kwargs):
        # Create a frame to hold treeview + scrollbars
        self.frame = ttk.Frame(parent)
        super().__init__(self.frame, show="headings", **kwargs)

        vsb = ttk.Scrollbar(self.frame, orient="vertical", command=self.yview)
        hsb = ttk.Scrollbar(self.frame, orient="horizontal", command=self.xview)
        self.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.frame.grid_rowconfigure(0, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)

    def set_data(self, headers, rows):
        # Clear existing
        self.delete(*self.get_children())
        self["columns"] = headers
        for h in headers:
            self.heading(h, text=h.upper())
            self.column(h, width=120, anchor="center")

        for row in rows:
            self.insert("", tk.END, values=row)

    def clear(self):
        self.delete(*self.get_children())
        self["columns"] = []


class DBMSUI:
    """Tkinter GUI for the SQL-DBMS engine."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SQL-DBMS Query Executor")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)

        # Initialize DBMS backend
        self.dbms = DBMS()
        self.sql_parser = self._load_parser()
        self.query_history = []

        self._build_ui()
        self._refresh_schema()

    def _load_parser(self):
        grammar_path = os.path.join(_SCRIPT_DIR, "grammar.lark")
        with open(grammar_path, "r", encoding="utf-8") as f:
            return Lark(f.read(), start="command", lexer="basic")

    # ----------------------------------------------------------------------- #
    # UI Construction
    # ----------------------------------------------------------------------- #
    def _build_ui(self):
        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        # Main layout: left sidebar + right workspace
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ---- Left Sidebar: Schema Browser ----
        left_frame = ttk.LabelFrame(main_paned, text="Schema Browser")
        main_paned.add(left_frame, weight=1)

        # Tables list
        ttk.Label(left_frame, text="Tables", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=4, pady=(4, 0))
        tables_frame = ttk.Frame(left_frame)
        tables_frame.pack(fill=tk.BOTH, expand=False, padx=4, pady=2)

        self.tables_listbox = tk.Listbox(tables_frame, exportselection=False, font=("Consolas", 10))
        self.tables_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tables_scroll = ttk.Scrollbar(tables_frame, orient="vertical", command=self.tables_listbox.yview)
        tables_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tables_listbox.config(yscrollcommand=tables_scroll.set)
        self.tables_listbox.bind("<<ListboxSelect>>", self._on_table_select)
        self.tables_listbox.bind("<Double-Button-1>", self._on_table_double_click)

        ttk.Separator(left_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=4, pady=4)

        # Table details
        ttk.Label(left_frame, text="Table Details", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=4)
        details_frame = ttk.Frame(left_frame)
        details_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.details_text = tk.Text(
            details_frame, wrap=tk.WORD, height=12, state=tk.DISABLED, font=("Consolas", 9)
        )
        self.details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        details_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=self.details_text.yview)
        details_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.details_text.config(yscrollcommand=details_scroll.set)

        # Refresh button
        ttk.Button(left_frame, text="Refresh Schema", command=self._refresh_schema).pack(anchor="w", padx=4, pady=4)

        # ---- Right Workspace: Editor + Results ----
        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=4)

        # Editor frame
        editor_frame = ttk.LabelFrame(right_paned, text="SQL Editor")
        right_paned.add(editor_frame, weight=1)

        # Toolbar
        toolbar = ttk.Frame(editor_frame)
        toolbar.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(toolbar, text="Execute (F5)", command=self._execute_query).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Clear", command=self._clear_editor).pack(side=tk.LEFT, padx=2)

        # History dropdown
        ttk.Label(toolbar, text="History:").pack(side=tk.LEFT, padx=(12, 2))
        self.history_var = tk.StringVar(value="")
        self.history_combo = ttk.Combobox(toolbar, textvariable=self.history_var, state="readonly", width=40)
        self.history_combo.pack(side=tk.LEFT, padx=2)
        self.history_combo.bind("<<ComboboxSelected>>", self._on_history_select)
        ttk.Button(toolbar, text="Load", command=self._on_history_select).pack(side=tk.LEFT, padx=2)

        # SQL editor widget
        self.editor = SQLText(editor_frame, wrap=tk.WORD, height=10, font=("Consolas", 11))
        self.editor.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.editor.bind("<F5>", lambda e: self._execute_query())
        self.editor.focus_set()

        # Results frame
        results_frame = ttk.LabelFrame(right_paned, text="Results")
        right_paned.add(results_frame, weight=2)

        # Notebook for result tabs
        self.result_notebook = ttk.Notebook(results_frame)
        self.result_notebook.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Tab: Data Grid
        self.grid_tab = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.grid_tab, text="Data Grid")
        self.result_tree = ResultTreeview(self.grid_tab)
        self.result_tree.frame.pack(fill=tk.BOTH, expand=True)

        # Tab: Text Output
        self.text_tab = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.text_tab, text="Text Output")
        self.output_text = tk.Text(
            self.text_tab, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10)
        )
        self.output_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Tab: Error Output
        self.error_tab = ttk.Frame(self.result_notebook)
        self.result_notebook.add(self.error_tab, text="Errors")
        self.error_text = tk.Text(
            self.error_tab, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10), fg="#B22222"
        )
        self.error_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Sample query placeholder
        self.editor.insert("1.0", "-- Type your SQL query here and press F5 or click Execute\n")

    # ----------------------------------------------------------------------- #
    # Actions
    # ----------------------------------------------------------------------- #
    def _refresh_schema(self):
        self.tables_listbox.delete(0, tk.END)
        try:
            output = self.dbms.show_tables()
            # Parse the formatted output: lines between ---- separators
            lines = output.strip().splitlines()
            in_tables = False
            for line in lines:
                if line.startswith("-") and len(line) >= 10:
                    in_tables = not in_tables
                    continue
                if in_tables and line.strip():
                    self.tables_listbox.insert(tk.END, line.strip())
        except Exception as e:
            self._set_error_text(f"Failed to load schema: {e}")

    def _on_table_select(self, event=None):
        selection = self.tables_listbox.curselection()
        if not selection:
            return
        table_name = self.tables_listbox.get(selection[0])
        try:
            table = self.dbms.explain_describe_desc(table_name)
            self.details_text.config(state=tk.NORMAL)
            self.details_text.delete("1.0", tk.END)
            self.details_text.insert(tk.END, str(table))
            self.details_text.config(state=tk.DISABLED)
        except Exception as e:
            self.details_text.config(state=tk.NORMAL)
            self.details_text.delete("1.0", tk.END)
            self.details_text.insert(tk.END, f"Error: {e}")
            self.details_text.config(state=tk.DISABLED)

    def _on_table_double_click(self, event=None):
        selection = self.tables_listbox.curselection()
        if not selection:
            return
        table_name = self.tables_listbox.get(selection[0])
        query = f"SELECT * FROM {table_name};"
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", query)
        self.editor._highlight()

    def _clear_editor(self):
        self.editor.delete("1.0", tk.END)
        self.editor.focus_set()

    def _show_shortcuts(self):
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "F5          Execute query\n"
            "Ctrl+A      Select all\n"
            "Tab         Insert 4 spaces\n"
            "Enter       Auto-indent",
        )

    def _add_to_history(self, query: str):
        cleaned = query.strip()
        if cleaned and cleaned not in self.query_history:
            self.query_history.insert(0, cleaned)
            if len(self.query_history) > 20:
                self.query_history.pop()
            self.history_combo.config(values=self.query_history)

    def _on_history_select(self, event=None):
        val = self.history_var.get()
        if val:
            self.editor.delete("1.0", tk.END)
            self.editor.insert("1.0", val)
            self.editor._highlight()

    def _set_output_text(self, text: str):
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self.output_text.config(state=tk.DISABLED)

    def _clear_results(self):
        self.result_tree.clear()
        self._set_output_text("")

    def _set_error_text(self, text: str):
        self.error_text.config(state=tk.NORMAL)
        self.error_text.delete("1.0", tk.END)
        self.error_text.insert(tk.END, text)
        self.error_text.config(state=tk.DISABLED)

    def _clear_errors(self):
        self.error_text.config(state=tk.NORMAL)
        self.error_text.delete("1.0", tk.END)
        self.error_text.config(state=tk.DISABLED)

    # ----------------------------------------------------------------------- #
    # Query Execution
    # ----------------------------------------------------------------------- #
    def _execute_query(self):
        raw = self.editor.get("1.0", tk.END).strip()
        if not raw:
            return

        # Ensure trailing semicolon for parser compatibility
        query_sequence = raw if raw.endswith(";") else raw + ";"
        self._add_to_history(raw)
        self._clear_errors()
        self.status_var.set("Executing...")
        self.root.update_idletasks()

        # Split query sequence into individual statements
        query_list = self._split_query_sequence(query_sequence)

        all_text_outputs = []
        any_grid = False

        for query in query_list:
            if not query.strip():
                continue
            try:
                result = self._run_single_query(query)
                if result is None:
                    continue  # exit or empty
                stmt_type, payload = result
                if stmt_type == "grid":
                    headers, rows = payload
                    if not any_grid:
                        self.result_tree.set_data(headers, rows)
                        any_grid = True
                    else:
                        # Append to existing grid
                        for row in rows:
                            self.result_tree.insert("", tk.END, values=row)
                elif stmt_type == "text":
                    all_text_outputs.append(payload)
            except Exception as e:
                self._clear_results()
                self._set_error_text(str(e))
                self.status_var.set(f"Error: {e}")
                self.result_notebook.select(self.error_tab)
                return

        if any_grid:
            self.result_notebook.select(self.grid_tab)
        elif all_text_outputs:
            self._set_output_text("\n".join(all_text_outputs))
            self.result_notebook.select(self.text_tab)
        else:
            self._set_output_text("Query executed successfully.")
            self.result_notebook.select(self.text_tab)

        self.status_var.set(f"Executed {len(query_list)} statement(s)")
        self._refresh_schema()

    def _split_query_sequence(self, input_query_sequence: str):
        input_query_sequence = input_query_sequence.rstrip()
        query_list = input_query_sequence.split(";")
        return [query.strip() + ";" for query in query_list if query.strip()]

    def _run_single_query(self, query: str):
        """Parse and execute a single SQL statement. Returns (type, payload)."""
        sql_transformer = SQLTransformer()
        statement, table, record, tables, select_columns, where = self._parse_query(
            self.sql_parser, sql_transformer, query
        )

        if statement == "exit":
            self.root.quit()
            return None

        if statement == "create table":
            success = self.dbms.create_table(table)
            return "text", str(success)

        elif statement == "drop table":
            success = self.dbms.drop_table(table["table_name"])
            return "text", str(success)

        elif statement in ("explain", "describe", "desc"):
            tbl = self.dbms.explain_describe_desc(table["table_name"])
            return "text", str(tbl)

        elif statement == "show tables":
            output = self.dbms.show_tables()
            return "text", output

        elif statement == "insert":
            result = self.dbms.insert(table, record)
            return "text", str(result)

        elif statement == "delete":
            result, extra = self.dbms.delete(table["table_name"], where)
            lines = [str(result)]
            if extra:
                lines.append(str(extra))
            return "text", "\n".join(lines)

        elif statement == "select":
            output = self.dbms.select(tables, select_columns, where)
            headers, rows = self._parse_select_output(output)
            return "grid", (headers, rows)

        elif statement == "create index":
            success = self.dbms.create_index(table["table_name"], table["index_name"], table["column_name"])
            return "text", str(success)

        elif statement == "drop index":
            success = self.dbms.drop_index(table["table_name"], table["index_name"])
            return "text", str(success)

        return "text", f"Unhandled statement: {statement}"

    def _parse_query(self, sql_parser, sql_transformer, query):
        try:
            parsed = sql_parser.parse(query)
        except Exception:
            raise SyntaxError()
        else:
            transformed = sql_transformer.transform(parsed)
            return transformed

    def _parse_select_output(self, output: str):
        """Parse the formatted ASCII table from dbms.select() into headers and rows."""
        lines = output.strip().splitlines()
        headers = []
        rows = []
        in_data = False

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("+-"):
                if headers and not in_data:
                    in_data = True
                continue
            if stripped.startswith("|"):
                parts = [p.strip() for p in stripped.split("|")][1:-1]
                if not headers:
                    headers = parts
                elif in_data:
                    rows.append(parts)
        return headers, rows


def main():
    root = tk.Tk()
    app = DBMSUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
