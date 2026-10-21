from tkinter import *
from customtkinter import *
import sqlite3
from datetime import datetime
import hashlib
import tkinter.messagebox as messagebox
from PIL import Image
from tkinter import filedialog
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import defaultdict
import os
import csv
import sys
import socket
import threading
import json

# ── Try importing PyGithub; disable GitHub features if missing ──────────────
try:
    from github import Github
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False
    print("PyGithub not installed – GitHub push disabled")

set_appearance_mode("light")
set_default_color_theme("blue")

# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE  –  connection pool
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = os.path.join(os.path.expanduser("~"), "Desktop", "election.db")

# SQLite connection cache: one connection per thread
_local = threading.local()


def get_connection():
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def db_execute(query, params=(), fetch=None, commit=False):
    """
    Thread-safe DB helper.

    fetch:
      None  – DML/DDL (commits automatically)
      "one" – SELECT returning one row or None
      "all" – SELECT returning list of rows
    """
    # Guard against accidental SELECT without fetch
    stripped = query.strip().upper()
    if fetch is None and stripped.startswith("SELECT"):
        raise ValueError(
            f"db_execute called with a SELECT query but fetch=None. "
            f"Pass fetch='one' or fetch='all'. Query: {query[:80]}"
        )

    conn = get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)

            if fetch == "one":
                result = cursor.fetchone()
            elif fetch == "all":
                result = cursor.fetchall()
            else:
                result = cursor.lastrowid

            if fetch is None or commit:
                conn.commit()

            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
    except Exception:
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  IN-MEMORY SETTINGS CACHE
#  BUG FIX #8: avoid 3 DB round-trips on every 30-second poll cycle by caching
#  app_settings values in memory.  Cache is invalidated whenever set_setting()
#  writes a new value.
# ─────────────────────────────────────────────────────────────────────────────
_MISSING = object()
_settings_cache: dict = {}
_settings_cache_lock = threading.Lock()


def _cache_invalidate(key_name: str):
    with _settings_cache_lock:
        _settings_cache.pop(key_name, None)


def _cache_get(key_name: str):
    with _settings_cache_lock:
        return _settings_cache.get(key_name, _MISSING)


def _cache_set(key_name: str, value):
    with _settings_cache_lock:
        _settings_cache[key_name] = value





# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class DatabaseManager:

    @staticmethod
    def initialize_database():
        try:
            db_execute("""
                CREATE TABLE IF NOT EXISTS app_passwords (
                    id TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL
                )
            """, commit=True)

            default_hash = hashlib.sha256("admin123".encode()).hexdigest()
            db_execute(
                "INSERT OR IGNORE INTO app_passwords (id, password_hash) VALUES (?, ?)",
                ("admin", default_hash), commit=True
            )
            db_execute(
                "INSERT OR IGNORE INTO app_passwords (id, password_hash) VALUES (?, ?)",
                ("multiplier", default_hash), commit=True
            )

            db_execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    image_path TEXT,
                    votes INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """, commit=True)

            db_execute("""
                CREATE TABLE IF NOT EXISTS voting_status (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    is_active INTEGER DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT
                )
            """, commit=True)

            db_execute(
                "INSERT OR IGNORE INTO voting_status (id, is_active) VALUES (1, 0)",
                commit=True
            )

            print("Database initialized successfully")
        except Exception as e:
            print(f"Database initialization error: {e}")

        try:
            DatabaseManager.initialize_settings_table()
        except Exception as e:
            print(f"Settings table initialization error: {e}")
            raise  # re-raise so ElectionApp.__init__ catches it and shows the error dialog

    @staticmethod
    def initialize_settings_table():
        db_execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key_name TEXT PRIMARY KEY,
                value TEXT
            )
        """, commit=True)

        defaults = [
            ('github_pat', ''),
            ('github_username', ''),
            ('github_repo', ''),
            ('push_interval', '60'),
            ('last_push_time', ''),
            ('last_push_status', ''),
            ('node_name', ''),
        ]
        for key, value in defaults:
            db_execute(
                "INSERT OR IGNORE INTO app_settings (key_name, value) VALUES (?, ?)",
                (key, value), commit=True
            )

    # ── Passwords ────────────────────────────────────────────────────────────

    @staticmethod
    def get_password_hash(password_id):
        row = db_execute(
            "SELECT password_hash FROM app_passwords WHERE id = ?",
            (password_id,), fetch="one"
        )
        return row[0] if row else None

    @staticmethod
    def set_password_hash(password_id, new_hash):
        db_execute(
            "UPDATE app_passwords SET password_hash = ? WHERE id = ?",
            (new_hash, password_id), commit=True
        )

    # ── Settings (with cache) ─────────────────────────────────────────────────

    @staticmethod
    def get_setting(key_name):
        # BUG FIX #8: serve from cache when available
        cached = _cache_get(key_name)
        if cached is not _MISSING:
            return cached
        row = db_execute(
            "SELECT value FROM app_settings WHERE key_name = ?",
            (key_name,), fetch="one"
        )
        value = row[0] if row else None
        _cache_set(key_name, value)
        return value

    @staticmethod
    def set_setting(key_name, value):
        db_execute(
            "UPDATE app_settings SET value = ? WHERE key_name = ?",
            (value, key_name), commit=True
        )
        # Invalidate cache entry so next read hits DB
        _cache_invalidate(key_name)

    # ── Candidates ───────────────────────────────────────────────────────────

    @staticmethod
    def get_candidates_by_category(category):
        return db_execute(
            "SELECT id, name, image_path, votes FROM candidates "
            "WHERE category = ? AND active = 1",
            (category,), fetch="all"
        )

    @staticmethod
    def add_candidate(name, category, image_path=""):
        return db_execute(
            "INSERT INTO candidates (name, category, image_path) VALUES (?, ?, ?)",
            (name, category, image_path), commit=True
        )

    @staticmethod
    def increment_candidate_votes(candidate_id, increment=1):
        db_execute(
            "UPDATE candidates SET votes = votes + ? WHERE id = ?",
            (increment, candidate_id), commit=True
        )

    @staticmethod
    def get_voting_status():
        row = db_execute(
            "SELECT is_active FROM voting_status WHERE id = 1",
            fetch="one"
        )
        return bool(row[0]) if row else False

    @staticmethod
    def set_voting_status(status):
        if status:
            db_execute(
                "UPDATE voting_status SET is_active = 1, started_at = datetime('now') WHERE id = 1",
                commit=True
            )
        else:
            db_execute(
                "UPDATE voting_status SET is_active = 0, ended_at = datetime('now') WHERE id = 1",
                commit=True
            )

    @staticmethod
    def get_all_candidates_with_votes():
        return db_execute(
            "SELECT c.id, c.name, c.category, c.votes, c.created_at "
            "FROM candidates c WHERE c.active = 1 ORDER BY c.category, c.votes DESC",
            fetch="all"
        )

    @staticmethod
    def get_voting_statistics():
        stats = {}

        stats['category_stats'] = db_execute("""
            SELECT category, SUM(votes) as total_votes, COUNT(*) as candidate_count
            FROM candidates WHERE active = 1 GROUP BY category
        """, fetch="all")

        stats['top_candidates'] = db_execute("""
            SELECT name, category, votes FROM candidates
            WHERE active = 1 ORDER BY votes DESC LIMIT 10
        """, fetch="all")

        row = db_execute(
            "SELECT SUM(votes) as total FROM candidates WHERE active = 1",
            fetch="one"
        )
        stats['total_votes'] = row[0] if (row and row[0]) else 0

        return stats

    @staticmethod
    def delete_candidate(candidate_id):
        db_execute(
            "UPDATE candidates SET active = 0 WHERE id = ?",
            (candidate_id,), commit=True
        )

    @staticmethod
    def reset_all_votes():
        db_execute("UPDATE candidates SET votes = 0", commit=True)

    @staticmethod
    def export_votes_csv(reason="auto"):
        """Export current vote counts to a timestamped CSV. Thread-safe."""
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop", "ElectionBackups")
            os.makedirs(desktop, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                node_name = DatabaseManager.get_setting('node_name') or socket.gethostname()
            except Exception:
                node_name = socket.gethostname()

            node_name = "".join(c for c in node_name if c.isalnum() or c in ('-', '_'))
            filename = f"{node_name}_votes_{reason}_{timestamp}.csv"
            filepath = os.path.join(desktop, filename)

            rows = db_execute(
                "SELECT name, category, votes FROM candidates "
                "WHERE active = 1 ORDER BY category, votes DESC",
                fetch="all"
            )

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Category", "Votes", "Exported At"])
                exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for name, category, votes in rows:
                    writer.writerow([name, category, votes, exported_at])

            print(f"Votes exported: {filepath}")
            return filepath
        except Exception as e:
            print(f"Export failed: {e}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
#  GITHUB MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class GitHubManager:

    @staticmethod
    def _credentials_configured():
        """
        BUG FIX #8: these three reads now hit the in-memory cache after the
        first call instead of making 3 DB round-trips every time.
        """
        pat      = DatabaseManager.get_setting('github_pat')
        username = DatabaseManager.get_setting('github_username')
        repo     = DatabaseManager.get_setting('github_repo')
        return bool(pat and username and repo)

    @staticmethod
    def get_github_client():
        if not GITHUB_AVAILABLE:
            return None, "PyGithub not installed"
        pat = DatabaseManager.get_setting('github_pat')
        if not pat:
            return None, "GitHub PAT not configured"
        try:
            return Github(pat), None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def get_repo():
        g, error = GitHubManager.get_github_client()
        if error:
            return None, error
        username = DatabaseManager.get_setting('github_username')
        repo_name = DatabaseManager.get_setting('github_repo')
        if not username or not repo_name:
            return None, "GitHub username or repo not configured"
        try:
            return g.get_user(username).get_repo(repo_name), None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def push_csv(filepath):
        if not filepath or not os.path.exists(filepath):
            msg = "Export file not found – push skipped"
            DatabaseManager.set_setting('last_push_status', msg)
            print(msg)
            return False
        try:
            repo, error = GitHubManager.get_repo()
            if error:
                DatabaseManager.set_setting('last_push_status', f"Failed: {error}")
                return False

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            filename = os.path.basename(filepath)
            repo_path = f"votes/{filename}"
            commit_msg = f"Update votes - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            # replace with:
            try:
                existing = repo.get_contents(repo_path)
                repo.update_file(repo_path, commit_msg, content, existing.sha)
            except Exception as e:
                # Only fall through to create_file for a genuine 404 (file doesn't exist yet).
                # Any other error (rate limit, permissions, network) should surface properly.
                status_code = getattr(e, 'status', None)
                if status_code == 404:
                    repo.create_file(repo_path, commit_msg, content)
                else:
                    raise

            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            DatabaseManager.set_setting('last_push_time', now)
            DatabaseManager.set_setting('last_push_status', f"Success at {now}")
            print(f"GitHub push successful: {filename}")
            return True

        except Exception as e:
            msg = f"Failed: {str(e)}"
            DatabaseManager.set_setting('last_push_status', msg)
            print(f"GitHub push error: {e}")
            return False

    @staticmethod
    def push_latest_csv():
        if not GitHubManager._credentials_configured():
            print("GitHub push skipped: credentials not configured")
            DatabaseManager.set_setting('last_push_status', 'Skipped: credentials not configured')
            return False
        filepath = DatabaseManager.export_votes_csv(reason="github_push")
        return GitHubManager.push_csv(filepath)

    @staticmethod
    def read_control_file(filename):
        if not GitHubManager._credentials_configured():
            return None
        repo, error = GitHubManager.get_repo()
        if error:
            return None
        try:
            contents = repo.get_contents(f"control/{filename}")
            return json.loads(contents.decoded_content.decode())
        except Exception:
            return None

    @staticmethod
    def write_heartbeat(node_name):
        if not GitHubManager._credentials_configured():
            return
        try:
            repo, error = GitHubManager.get_repo()
            if error:
                return
            payload = json.dumps({
                "node": node_name,
                "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "status": "online"
            })
            path = f"heartbeat/{node_name}.json"
            # In write_heartbeat, replace the inner try/except (lines 522-526):
            try:
                existing = repo.get_contents(path)
                repo.update_file(path, f"heartbeat {node_name}", payload, existing.sha)
            except Exception as e:
                status_code = getattr(e, 'status', None)
                if status_code == 404:
                    repo.create_file(path, f"heartbeat {node_name}", payload)
                else:
                    raise  # let the outer except log it
        except Exception as e:
            print(f"Heartbeat write error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN PANEL
# ─────────────────────────────────────────────────────────────────────────────

class AdminPanel:

    def __init__(self, parent_app):
        self.parent_app = parent_app
        self.admin_window = None
        self.categories = ["HEAD BOY", "HEAD GIRL", "ASSISTANT HEAD BOY", "ASSISTANT HEAD GIRL"]

    def _center(self, win, w, h):
        win.update_idletasks()
        x = (win.winfo_screenwidth() // 2) - (w // 2)
        y = (win.winfo_screenheight() // 2) - (h // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def browse_image_file(self, entry_widget):
        file_path = filedialog.askopenfilename(
            title="Select Candidate Image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.tiff"),
                ("All files", "*.*")
            ],
        )
        if file_path:
            entry_widget.delete(0, 'end')
            entry_widget.insert(0, file_path)

    # ── Login ─────────────────────────────────────────────────────────────────

    def show_login(self):
        win = CTkToplevel(self.parent_app.root)
        win.title("Admin Login")
        win.resizable(False, False)
        self._center(win, 400, 300)
        win.grab_set()

        CTkLabel(win, text="Admin Access",
                 font=CTkFont(size=24, weight="bold")).pack(pady=30)
        CTkLabel(win, text="Enter Admin Password:",
                 font=CTkFont(size=14)).pack(pady=10)

        pw_entry = CTkEntry(win, show="*", width=250, height=40, font=CTkFont(size=14))
        pw_entry.pack(pady=10)
        pw_entry.focus()

        err = CTkLabel(win, text="", text_color="red")
        err.pack(pady=5)

        def verify():
            entered = pw_entry.get()
            if not entered:
                err.configure(text="Password cannot be empty!")
                return
            if hashlib.sha256(entered.encode()).hexdigest() == DatabaseManager.get_password_hash("admin"):
                win.destroy()
                self.show_admin_panel()
            else:
                err.configure(text="Invalid password!")
                pw_entry.delete(0, END)

        pw_entry.bind("<Return>", lambda e: verify())
        CTkButton(win, text="Login", command=verify, width=150, height=40).pack(pady=20)
        CTkButton(win, text="Cancel", command=win.destroy, width=150, height=40).pack(pady=5)

    # ── GitHub Settings ───────────────────────────────────────────────────────

    def show_github_settings(self):
        if not GITHUB_AVAILABLE:
            messagebox.showwarning("GitHub Unavailable",
                                   "PyGithub is not installed.\n"
                                   "Run: pip install PyGithub")
            return

        win = CTkToplevel(self.admin_window)
        win.title("GitHub Settings")
        win.resizable(True, True)
        self._center(win, 500, 720)
        win.grab_set()

        CTkLabel(win, text="GitHub Settings",
                 font=CTkFont(size=24, weight="bold")).pack(pady=20)

        def make_field(label, default, show=None):
            CTkLabel(win, text=label, font=CTkFont(size=14)).pack(pady=(10, 5))
            kw = dict(width=350, height=35)
            if show:
                kw['show'] = show
            e = CTkEntry(win, **kw)
            e.insert(0, DatabaseManager.get_setting(default) or '')
            e.pack()
            return e

        pat_entry      = make_field("Personal Access Token:", 'github_pat', show="*")
        node_entry     = make_field("Node Name:", 'node_name')
        username_entry = make_field("GitHub Username:", 'github_username')
        repo_entry     = make_field("Repository Name:", 'github_repo')
        interval_entry = make_field("Push Interval (minutes):", 'push_interval')
        if not interval_entry.get():
            interval_entry.insert(0, '60')

        CTkLabel(win, text="Last Push Status:", font=CTkFont(size=14)).pack(pady=(15, 5))
        last_status = DatabaseManager.get_setting('last_push_status') or 'Never pushed'
        sc = "green" if "Success" in last_status else ("red" if "Failed" in last_status else "gray")
        CTkLabel(win, text=last_status, font=CTkFont(size=12), text_color=sc).pack()

        err = CTkLabel(win, text="", text_color="red")
        err.pack(pady=5)

        def save():
            pat      = pat_entry.get().strip()
            username = username_entry.get().strip()
            repo     = repo_entry.get().strip()
            interval = interval_entry.get().strip()
            if not all([pat, username, repo, interval]):
                err.configure(text="All fields are required!")
                return
            if not interval.isdigit() or int(interval) < 1:
                err.configure(text="Interval must be a positive number!")
                return
            DatabaseManager.set_setting('github_pat', pat)
            DatabaseManager.set_setting('github_username', username)
            DatabaseManager.set_setting('github_repo', repo)
            DatabaseManager.set_setting('push_interval', interval)
            DatabaseManager.set_setting('node_name', node_entry.get().strip())
            self.parent_app.restart_github_scheduler()
            messagebox.showinfo("Success", "GitHub settings saved!")
            win.destroy()

        def manual_push():
            # BUG FIX #1: initial label update is on main thread (correct).
            # All subsequent widget touches from the worker go via win.after(0,...).
            err.configure(text="Pushing...", text_color="blue")
            win.update()

            def _push():
                success = GitHubManager.push_latest_csv()
                if not win.winfo_exists():
                    return
                if success:
                    win.after(0, lambda: err.configure(
                        text="Push successful!", text_color="green"))
                else:
                    status = DatabaseManager.get_setting('last_push_status') or "Unknown error"
                    win.after(0, lambda s=status: err.configure(
                        text=str(s), text_color="red"))

            threading.Thread(target=_push, daemon=True).start()

        CTkButton(win, text="Save Settings", command=save,
                  width=200, height=40).pack(pady=15)
        CTkButton(win, text="Push Now", command=manual_push,
                  width=200, height=40, fg_color="#059669", hover_color="#047857").pack(pady=5)
        CTkButton(win, text="Close", command=win.destroy,
                  width=200, height=40, fg_color="gray").pack(pady=5)

    # ── Change Password ───────────────────────────────────────────────────────

    def show_change_password(self):
        win = CTkToplevel(self.admin_window)
        win.title("Change Password")
        win.resizable(False, False)
        self._center(win, 400, 550)
        win.grab_set()

        CTkLabel(win, text="Change Password",
                 font=CTkFont(size=24, weight="bold")).pack(pady=20)

        CTkLabel(win, text="Select Password to Change:", font=CTkFont(size=14)).pack(pady=(10, 5))
        pw_type = StringVar(value="admin")
        CTkRadioButton(win, text="Admin Password",
                       variable=pw_type, value="admin").pack(pady=3)
        CTkRadioButton(win, text="Vote Multiplier Password",
                       variable=pw_type, value="multiplier").pack(pady=3)

        CTkLabel(win, text="Current Password:", font=CTkFont(size=14)).pack(pady=(15, 5))
        current_e = CTkEntry(win, show="*", width=250, height=35)
        current_e.pack()

        CTkLabel(win, text="New Password:", font=CTkFont(size=14)).pack(pady=(15, 5))
        new_e = CTkEntry(win, show="*", width=250, height=35)
        new_e.pack()

        CTkLabel(win, text="Confirm New Password:", font=CTkFont(size=14)).pack(pady=(15, 5))
        confirm_e = CTkEntry(win, show="*", width=250, height=35)
        confirm_e.pack()

        err = CTkLabel(win, text="", text_color="red")
        err.pack(pady=5)

        def verify_and_change():
            current = current_e.get()
            new_pass = new_e.get()
            confirm  = confirm_e.get()
            p_type   = pw_type.get()

            current_hash = hashlib.sha256(current.encode()).hexdigest()
            stored_hash = DatabaseManager.get_password_hash(p_type)
            if current_hash != stored_hash:
                label = "Admin" if p_type == "admin" else "Vote Multiplier"
                err.configure(text=f"Current {label} password is incorrect!")
                return
            if not new_pass:
                err.configure(text="New password cannot be empty!")
                return
            if len(new_pass) < 6:
                err.configure(text="Password must be at least 6 characters!")
                return
            if new_pass != confirm:
                err.configure(text="New passwords do not match!")
                return

            new_hash = hashlib.sha256(new_pass.encode()).hexdigest()
            DatabaseManager.set_password_hash(p_type, new_hash)
            label = "Admin" if p_type == "admin" else "Vote Multiplier"
            messagebox.showinfo("Success", f"{label} password changed successfully!")
            win.destroy()

        CTkButton(win, text="Change Password", command=verify_and_change,
                  width=200, height=40).pack(pady=15)

    # ── Manual export ─────────────────────────────────────────────────────────

    def manual_export(self):
        filepath = DatabaseManager.export_votes_csv(reason="manual")
        if filepath:
            messagebox.showinfo("Export Successful", f"Votes exported to:\n{filepath}")
        else:
            messagebox.showerror("Export Failed",
                                 "Could not export votes. Check console for details.")

    # ── Admin Panel Main Window ───────────────────────────────────────────────

    def show_admin_panel(self):
        self.admin_window = CTkToplevel(self.parent_app.root)
        self.admin_window.title("Election Admin Panel")
        self._center(self.admin_window, 1000, 700)
        self.admin_window.grab_set()

        # BUG FIX #11: close any open matplotlib figures when the admin window
        # is destroyed, preventing renderer memory leaks.
        self.admin_window.bind("<Destroy>", lambda e: plt.close('all'))

        header = CTkFrame(self.admin_window, height=80, corner_radius=0,
                          fg_color=("#1e3a8a", "#1e40af"))
        header.pack(fill="x")
        header.pack_propagate(False)
        CTkLabel(header, text="Election Administration Panel",
                 font=CTkFont(size=28, weight="bold"),
                 text_color="white").pack(pady=25)

        main_frame = CTkFrame(self.admin_window, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        left = CTkFrame(main_frame, width=300, corner_radius=10)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        CTkLabel(left, text="Voting Control",
                 font=CTkFont(size=18, weight="bold")).pack(pady=(20, 10))
        self.voting_status_label = CTkLabel(left, text="", font=CTkFont(size=14))
        self.voting_status_label.pack(pady=5)
        self.toggle_voting_btn = CTkButton(left, text="", command=self.toggle_voting,
                                           width=200, height=40)
        self.toggle_voting_btn.pack(pady=10)

        CTkLabel(left, text="Candidate Management",
                 font=CTkFont(size=18, weight="bold")).pack(pady=(30, 10))
        CTkButton(left, text="Add Candidate", command=self.show_add_candidate,
                  width=200, height=40).pack(pady=5)
        CTkButton(left, text="View Statistics", command=self.show_voting_info,
                  width=200, height=40,
                  fg_color="#0369a1", hover_color="#075985").pack(pady=5)
        CTkButton(left, text="Reset All Votes", command=self.reset_votes,
                  width=200, height=40,
                  fg_color="orange", hover_color="darkorange").pack(pady=5)
        CTkButton(left, text="GitHub Settings", command=self.show_github_settings,
                  width=200, height=40,
                  fg_color="#24292e", hover_color="#1a1e22").pack(pady=5)
        CTkButton(left, text="Export Votes Now", command=self.manual_export,
                  width=200, height=40,
                  fg_color="#059669", hover_color="#047857").pack(pady=5)
        CTkButton(left, text="Change Password", command=self.show_change_password,
                  width=200, height=40,
                  fg_color="#7c3aed", hover_color="#6d28d9").pack(pady=5)
        CTkButton(left, text="Close Admin Panel", command=self.admin_window.destroy,
                  width=200, height=40,
                  fg_color="red", hover_color="darkred").pack(pady=(30, 20))

        right = CTkFrame(main_frame, corner_radius=10)
        right.pack(side="right", fill="both", expand=True)
        CTkLabel(right, text="Current Candidates",
                 font=CTkFont(size=18, weight="bold")).pack(pady=(20, 10))
        self.candidates_frame = CTkScrollableFrame(right, height=500)
        self.candidates_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.update_voting_status_display()
        self.refresh_candidates_list()

    def update_voting_status_display(self):
        if not hasattr(self, 'voting_status_label'):
            return
        is_active = DatabaseManager.get_voting_status()
        if is_active:
            self.voting_status_label.configure(text="Status: VOTING ACTIVE", text_color="green")
            self.toggle_voting_btn.configure(text="Stop Voting",
                                             fg_color="red", hover_color="darkred")
        else:
            self.voting_status_label.configure(text="Status: VOTING STOPPED", text_color="red")
            self.toggle_voting_btn.configure(text="Start Voting",
                                             fg_color="green", hover_color="darkgreen")

    def toggle_voting(self):
        current = DatabaseManager.get_voting_status()
        DatabaseManager.set_voting_status(not current)
        self.update_voting_status_display()
        # BUG FIX #12: always call update_voting_access via root.after so it is
        # guaranteed to run on the main thread, matching the remote-control path.
        if hasattr(self.parent_app, 'update_voting_access'):
            self.parent_app.root.after(0, self.parent_app.update_voting_access)
        status_text = "started" if not current else "stopped"
        messagebox.showinfo("Voting Status", f"Voting has been {status_text}!")

    # ── Add / Delete candidate ────────────────────────────────────────────────

    def show_add_candidate(self):
        win = CTkToplevel(self.admin_window)
        win.title("Add Candidate")
        self._center(win, 400, 420)
        win.grab_set()

        CTkLabel(win, text="Add New Candidate",
                 font=CTkFont(size=20, weight="bold")).pack(pady=20)

        CTkLabel(win, text="Candidate Name:").pack(pady=(10, 5))
        name_e = CTkEntry(win, width=250, height=35)
        name_e.pack(pady=5)

        CTkLabel(win, text="Category:").pack(pady=(15, 5))
        cat_var = StringVar(value=self.categories[0])
        CTkOptionMenu(win, values=self.categories, variable=cat_var, width=250).pack(pady=5)

        CTkLabel(win, text="Select Image (optional):").pack(pady=(15, 5))
        img_frame = CTkFrame(win, fg_color="transparent")
        img_frame.pack(pady=5)
        img_e = CTkEntry(img_frame, width=180, height=35, placeholder_text="No image selected")
        img_e.pack(side="left", padx=(0, 5))
        CTkButton(img_frame, text="Browse",
                  command=lambda: self.browse_image_file(img_e),
                  width=60, height=35,
                  fg_color="#059669", hover_color="#047857").pack(side="left")

        def add_candidate():
            name = name_e.get().strip()
            if not name:
                messagebox.showerror("Error", "Please enter candidate name!")
                return
            try:
                DatabaseManager.add_candidate(name, cat_var.get(), img_e.get().strip())
                messagebox.showinfo("Success", f"Candidate '{name}' added successfully!")
                win.destroy()
                self.refresh_candidates_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add candidate: {e}")

        CTkButton(win, text="Add Candidate", command=add_candidate,
                  width=150, height=40).pack(pady=20)
        CTkButton(win, text="Cancel", command=win.destroy,
                  width=150, height=40).pack(pady=5)

    def refresh_candidates_list(self):
        if not hasattr(self, 'candidates_frame'):
            return
        for w in self.candidates_frame.winfo_children():
            w.destroy()

        for category in self.categories:
            candidates = DatabaseManager.get_candidates_by_category(category)
            cat_f = CTkFrame(self.candidates_frame, fg_color="transparent")
            cat_f.pack(fill="x", pady=(10, 5))
            CTkLabel(cat_f, text=category,
                     font=CTkFont(size=16, weight="bold"),
                     text_color="#1e40af").pack(anchor="w")

            if not candidates:
                CTkLabel(cat_f, text="No candidates added",
                         text_color="gray").pack(anchor="w", padx=20)
            else:
                for cid, name, image_path, votes in candidates:
                    cf = CTkFrame(self.candidates_frame)
                    cf.pack(fill="x", pady=2, padx=10)
                    inf = CTkFrame(cf, fg_color="transparent")
                    inf.pack(side="left", fill="x", expand=True, padx=10, pady=10)
                    CTkLabel(inf, text=name,
                             font=CTkFont(size=14, weight="bold")).pack(anchor="w")
                    CTkLabel(inf, text=f"Votes: {votes}",
                             font=CTkFont(size=12)).pack(anchor="w")
                    if image_path:
                        CTkLabel(inf, text=f"Image: {image_path}",
                                 font=CTkFont(size=10), text_color="gray").pack(anchor="w")
                    CTkButton(cf, text="Delete",
                              command=lambda c=cid, n=name: self.delete_candidate(c, n),
                              width=80, height=30,
                              fg_color="red", hover_color="darkred").pack(
                                  side="right", padx=10, pady=10)

    def delete_candidate(self, candidate_id, candidate_name):
        if messagebox.askyesno("Confirm Delete",
                               f"Are you sure you want to delete '{candidate_name}'?"):
            try:
                DatabaseManager.delete_candidate(candidate_id)
                messagebox.showinfo("Success", f"Candidate '{candidate_name}' deleted!")
                self.refresh_candidates_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete candidate: {e}")

    def reset_votes(self):
        if messagebox.askyesno("Confirm Reset",
                               "Are you sure you want to reset all votes to 0?"):
            try:
                DatabaseManager.reset_all_votes()
                messagebox.showinfo("Success", "All votes have been reset to 0!")
                self.refresh_candidates_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to reset votes: {e}")

    # ── Statistics / Graphs ───────────────────────────────────────────────────

    def show_voting_info(self):
        win = CTkToplevel(self.admin_window)
        win.title("Voting Information & Statistics")
        self._center(win, 1200, 800)
        win.grab_set()

        # BUG FIX #11: close all matplotlib figures when stats window closes.
        win.bind("<Destroy>", lambda e: plt.close('all'))

        header = CTkFrame(win, height=60, corner_radius=0,
                          fg_color=("#1e3a8a", "#1e40af"))
        header.pack(fill="x")
        header.pack_propagate(False)
        CTkLabel(header, text="Voting Statistics & Analysis",
                 font=CTkFont(size=24, weight="bold"),
                 text_color="white").pack(pady=20)

        nb = CTkFrame(win)
        nb.pack(fill="both", expand=True, padx=20, pady=20)

        tab_f = CTkFrame(nb, height=50)
        tab_f.pack(fill="x", pady=(0, 10))
        tab_f.pack_propagate(False)

        content_f = CTkFrame(nb)
        content_f.pack(fill="both", expand=True)

        for txt, name in [("Summary", "summary"), ("Detailed View", "detailed"), ("Graphs", "graphs")]:
            CTkButton(tab_f, text=txt,
                      command=lambda n=name, cf=content_f: self.switch_tab(n, cf),
                      width=100, height=35).pack(side="left", padx=5, pady=7)

        self.switch_tab("summary", content_f)

    def switch_tab(self, tab_name, content_frame):
        for w in content_frame.winfo_children():
            if hasattr(w, '_fig'):
                try:
                    plt.close(w._fig)
                except Exception:
                    pass
            w.destroy()
        if tab_name == "summary":
            self.create_summary_tab(content_frame)
        elif tab_name == "detailed":
            self.create_detailed_tab(content_frame)
        elif tab_name == "graphs":
            self.create_graphs_tab(content_frame)

    def create_summary_tab(self, parent):
        sf = CTkScrollableFrame(parent)
        sf.pack(fill="both", expand=True, padx=10, pady=10)
        stats = DatabaseManager.get_voting_statistics()

        CTkLabel(sf, text="Overall Statistics",
                 font=CTkFont(size=20, weight="bold")).pack(pady=(0, 10))
        CTkFrame(sf).pack(fill="x", pady=(0, 20))
        CTkLabel(sf, text=f"Total Votes Cast: {stats['total_votes']}",
                 font=CTkFont(size=16)).pack(pady=5)

        CTkLabel(sf, text="Category-wise Statistics",
                 font=CTkFont(size=18, weight="bold")).pack(pady=(20, 10))
        for category, total_votes, candidate_count in (stats['category_stats'] or []):
            cf = CTkFrame(sf)
            cf.pack(fill="x", pady=5)
            CTkLabel(cf, text=category,
                     font=CTkFont(size=14, weight="bold")).pack(pady=2)
            CTkLabel(cf,
                     text=f"Total Votes: {total_votes} | Candidates: {candidate_count}",
                     font=CTkFont(size=12)).pack(pady=2)

        if stats['top_candidates']:
            CTkLabel(sf, text="Top Candidates",
                     font=CTkFont(size=18, weight="bold")).pack(pady=(20, 10))
            for i, (name, category, votes) in enumerate(stats['top_candidates'][:5], 1):
                tf = CTkFrame(sf)
                tf.pack(fill="x", pady=2)
                CTkLabel(tf, text=f"{i}. {name} ({category}) - {votes} votes",
                         font=CTkFont(size=14)).pack(pady=5)

    def create_detailed_tab(self, parent):
        sf = CTkScrollableFrame(parent)
        sf.pack(fill="both", expand=True, padx=10, pady=10)
        candidates = DatabaseManager.get_all_candidates_with_votes()
        groups = defaultdict(list)
        for cid, name, category, votes, created_at in candidates:
            groups[category].append((name, votes, created_at))

        for category in self.categories:
            if category in groups:
                CTkLabel(sf, text=category,
                         font=CTkFont(size=18, weight="bold"),
                         text_color="#1e40af").pack(pady=(20, 10), anchor="w")
                for name, votes, created_at in groups[category]:
                    cf = CTkFrame(sf)
                    cf.pack(fill="x", pady=2)
                    info = f"{name} - {votes} votes"
                    if created_at:
                        info += f" (Added: {created_at.strftime('%Y-%m-%d %H:%M')})"
                    CTkLabel(cf, text=info,
                             font=CTkFont(size=14)).pack(pady=8, padx=10, anchor="w")

    def create_graphs_tab(self, parent):
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle('Voting Statistics', fontsize=16, fontweight='bold')

        try:
            candidates = DatabaseManager.get_all_candidates_with_votes()
            stats      = DatabaseManager.get_voting_statistics()

            categories = defaultdict(list)
            all_names  = []
            all_votes  = []

            for cid, name, category, votes, created_at in candidates:
                categories[category].append((name, votes))
                all_names.append(name[:12] + ('…' if len(name) > 12 else ''))
                all_votes.append(votes)

            if all_names:
                bars = ax1.bar(range(len(all_names)), all_votes, color='skyblue', alpha=0.8)
                ax1.set_title('Votes by Candidate')
                ax1.set_xticks(range(len(all_names)))
                ax1.set_xticklabels(all_names, rotation=45, ha='right')
                for bar, v in zip(bars, all_votes):
                    ax1.text(bar.get_x() + bar.get_width() / 2,
                             bar.get_height() + 0.1, str(v), ha='center', va='bottom')

            if stats['category_stats']:
                names_pie  = [c for c, v, _ in stats['category_stats'] if v and v > 0]
                votes_pie  = [v for c, v, _ in stats['category_stats'] if v and v > 0]
                if votes_pie:
                    ax2.pie(votes_pie, labels=names_pie, autopct='%1.1f%%', startangle=90)
                    ax2.set_title('Vote Distribution by Category')
                else:
                    ax2.text(0.5, 0.5, 'No votes yet', ha='center', va='center',
                             transform=ax2.transAxes)
                    ax2.set_title('Vote Distribution by Category')

            if all_names:
                sorted_data = sorted(zip(all_names, all_votes), key=lambda x: x[1], reverse=True)
                top_names  = [n for n, v in sorted_data[:8]]
                top_votes  = [v for n, v in sorted_data[:8]]
                colors = plt.cm.Set2.colors
                ax3.barh(range(len(top_names)), top_votes,
                         color=[colors[i % len(colors)] for i in range(len(top_names))], alpha=0.8)
                ax3.set_yticks(range(len(top_names)))
                ax3.set_yticklabels(top_names)
                ax3.invert_yaxis()
                ax3.set_xlabel('Votes')
                ax3.set_title('Top Candidates')

            if stats['category_stats']:
                cat_names  = [c for c, v, n in stats['category_stats']]
                cat_votes  = [v or 0 for c, v, n in stats['category_stats']]
                cat_counts = [n for c, v, n in stats['category_stats']]
                x     = range(len(cat_names))
                w     = 0.35
                bars1 = ax4.bar([i - w/2 for i in x], cat_votes,  w,
                                label='Total Votes', color='lightblue', alpha=0.8)
                bars2 = ax4.bar([i + w/2 for i in x], cat_counts, w,
                                label='No. Candidates', color='lightgreen', alpha=0.8)
                ax4.set_xticks(list(x))
                ax4.set_xticklabels(
                    [c[:15] + ('…' if len(c) > 15 else '') for c in cat_names],
                    rotation=45, ha='right')
                ax4.set_title('Votes vs Candidates by Category')
                ax4.legend()
                for bar in bars1 + bars2:
                    h = bar.get_height()
                    ax4.text(bar.get_x() + bar.get_width()/2, h + 0.1,
                             str(int(h)), ha='center', va='bottom', fontsize=8)

            plt.tight_layout()

        except Exception as e:
            print(f"Graph error: {e}")

        canvas = FigureCanvasTkAgg(fig, parent)
        canvas.draw()
        tk_widget = canvas.get_tk_widget()
        tk_widget._fig = fig
        tk_widget.pack(fill="both", expand=True, padx=10, pady=10)


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class ElectionApp:

    def __init__(self):
        try:
            DatabaseManager.initialize_database()
        except Exception as e:
            import tkinter as tk
            _r = tk.Tk()
            _r.withdraw()
            messagebox.showerror(
                "Database Error",
                f"Could not connect to the database:\n\n{e}\n\nPlease make sure MySQL is running and try again."
            )
            _r.destroy()
            sys.exit(1)

        self.root = CTk()
        self.root.geometry("1400x900")
        self.root.title("BVBVM Election Management System")
        self.root.configure(fg_color="#f0f4f8")

        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - 700
        y = (self.root.winfo_screenheight() // 2) - 450
        self.root.geometry(f"1400x900+{x}+{y}")

        try:
            icon = PhotoImage(file='bvblogo2.png')
            self.root.iconphoto(True, icon)
        except Exception:
            pass

        self.current_category = 0
        self.categories = ["HEAD BOY", "HEAD GIRL",
                           "ASSISTANT HEAD BOY", "ASSISTANT HEAD GIRL"]
        self.current_candidates = []
        self._voted_categories = set()  # tracks which category indices have been voted in this session
        self.vote_multiplier_active = False
        self.vote_multiplier = 1

        self._next_cat_job = None
        self._candidate_images = []

        self.admin_panel = AdminPanel(self)
        self._github_job = None
        self._export_job = None

        self._shutdown_lock = threading.Lock()
        self._shutdown_exported = False

        self._last_export_ts = None
        self._announcement_text = None
        self._announcement_widget = None
        self._heartbeat_job = None
        self._control_poll_job = None

        self._control_lock = threading.Lock()

        self.setup_ui()
        self.update_voting_access()
        self.setup_exit_handlers()

        self.root.after(5000, self.start_auto_export)
        self.start_github_scheduler()
        self.root.after(5000, self._start_control_poll)
        #self.root.after(10000, self._start_heartbeat)

    # ── UI Setup ──────────────────────────────────────────────────────────────

    def setup_ui(self):
        self.header_frame = CTkFrame(self.root, height=120, corner_radius=0,
                                     fg_color=("#1e3a8a", "#1e40af"))
        self.header_frame.pack(fill="x")
        self.header_frame.pack_propagate(False)

        title_container = CTkFrame(self.header_frame, fg_color="transparent")
        title_container.pack(expand=True, fill="both")

        CTkLabel(title_container, text="BVBVM ELECTION SYSTEM 2025",
                 font=CTkFont(family="Segoe UI", size=36, weight="bold"),
                 text_color="white").pack(pady=(20, 5))
        CTkLabel(title_container, text="Digital Voting Platform",
                 font=CTkFont(family="Segoe UI", size=16),
                 text_color="#cbd5e1").pack()

        self.admin_btn = CTkButton(self.header_frame, text="Admin",
                                   command=self.admin_panel.show_login,
                                   width=80, height=30,
                                   fg_color="transparent", border_width=1,
                                   border_color="white", text_color="white")
        self.admin_btn.place(relx=0.95, rely=0.2, anchor="ne")

        self.multiplier_btn = CTkButton(self.header_frame, text="10x Vote",
                                        command=self.show_multiplier_login,
                                        width=80, height=30,
                                        fg_color="transparent", border_width=1,
                                        border_color="white", text_color="white")
        self.multiplier_btn.place(relx=0.88, rely=0.2, anchor="ne")

        self.status_frame = CTkFrame(self.root, height=50, corner_radius=0,
                                     fg_color=("#e2e8f0", "#f1f5f9"))
        self.status_frame.pack(fill="x")
        self.status_frame.pack_propagate(False)

        self.status_left = CTkLabel(self.status_frame,
                                    text=f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                                    font=CTkFont(family="Segoe UI", size=12),
                                    text_color="#64748b")
        self.status_left.pack(side="left", padx=20, pady=15)

        self.status_right = CTkLabel(self.status_frame, text="Status: Ready",
                                     font=CTkFont(family="Segoe UI", size=12),
                                     text_color="#059669")
        self.status_right.pack(side="right", padx=20, pady=15)

        # BUG FIX #6: dedicated banner slot instead of pack(after=status_frame).
        # We reserve a zero-height frame here and show/hide content inside it,
        # so layout order is always deterministic and pack(after=...) is never needed.
        self._banner_slot = CTkFrame(self.root, height=0, corner_radius=0,
                                     fg_color="transparent")
        self._banner_slot.pack(fill="x")

        self.main_frame = CTkFrame(self.root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=30, pady=20)

        self.category_frame = CTkFrame(self.main_frame, height=80, corner_radius=15,
                                       fg_color=("#ffffff", "#f8fafc"),
                                       border_width=1, border_color="#e2e8f0")
        self.category_frame.pack(fill="x", pady=(0, 20))
        self.category_frame.pack_propagate(False)

        self.category_label = CTkLabel(self.category_frame, text="",
                                       font=CTkFont(family="Segoe UI", size=28, weight="bold"),
                                       text_color="#1e40af")
        self.category_label.pack(expand=True)

        self.voting_container = CTkFrame(self.main_frame, fg_color="transparent")
        self.voting_container.pack(fill="both", expand=True, anchor="n")

        self.candidate_card = CTkFrame(self.voting_container,
                                       width=500, height=650,
                                       corner_radius=20,
                                       fg_color=("#ffffff", "#f8fafc"),
                                       border_width=2, border_color="#e2e8f0")
        self.candidate_card.pack(expand=True, anchor="n", pady=20) # use pack instead of place

    # ── Voting access control ─────────────────────────────────────────────────

    def update_voting_access(self):
        try:
            is_active = DatabaseManager.get_voting_status()
        except Exception as e:
            print(f"Could not read voting status: {e}")
            is_active = False

        if is_active:
            if self.current_category >= len(self.categories):
                self.current_category = 0
                self._voted_categories = set()

            # Only re-render if we're not already showing a live voting screen.
            # Avoids disrupting a voter mid-session when the poll fires.
            current_text = self.category_label.cget("text")
            already_voting = any(
                f"SELECT {cat}" in current_text for cat in self.categories
            )
            if not already_voting:
                self.display_category()

            self.status_right.configure(text="Status: Voting Active", text_color="#059669")
        else:
            if self.vote_multiplier_active:
                self.vote_multiplier_active = False
                self.vote_multiplier = 1
                self.multiplier_btn.configure(fg_color="transparent",
                                              border_color="white", text="10x Vote")
            self.show_voting_inactive_message()
            self.status_right.configure(text="Status: Voting Inactive", text_color="#dc2626")
    # ── GitHub scheduler ──────────────────────────────────────────────────────
    def _clear_voting_container(self):
        """Destroy all children of voting_container except candidate_card,
        and cancel any pending next_category job."""
        if self._next_cat_job is not None:
            self.root.after_cancel(self._next_cat_job)
            self._next_cat_job = None
        for w in self.voting_container.winfo_children():
            if w != self.candidate_card:
                w.destroy()
        self.candidate_card.pack_forget()
    def start_github_scheduler(self):
        try:
            minutes = int(DatabaseManager.get_setting('push_interval') or 60)
            if minutes < 1:
                raise ValueError
        except Exception:
            # BUG FIX #13: log when the stored value is invalid so operators
            # can diagnose a corrupted DB row rather than silently falling back.
            print("WARNING: push_interval setting is invalid or missing; "
                  "defaulting to 60 minutes.")
            minutes = 60
        interval_ms = minutes * 60 * 1000
        self._github_job = self.root.after(interval_ms, self._scheduled_github_push)
        print(f"GitHub push scheduled every {minutes} minutes")

    def restart_github_scheduler(self):
        if self._github_job:
            self.root.after_cancel(self._github_job)
        self.start_github_scheduler()

    def _scheduled_github_push(self):
        def _push():
            if GitHubManager._credentials_configured():
                GitHubManager.push_latest_csv()
            else:
                print("Scheduled GitHub push skipped: credentials not configured")
        threading.Thread(target=_push, daemon=True).start()
        self.start_github_scheduler()

    # ── Auto export ───────────────────────────────────────────────────────────

    def start_auto_export(self):
        def _export():
            filepath = DatabaseManager.export_votes_csv(reason="auto")
            if not filepath:
                # Show a warning on the main thread so the operator notices
                self.root.after(0, lambda: self.update_status("Auto-export FAILED – check console", "#dc2626"))

        threading.Thread(target=_export, daemon=True).start()
        self._export_job = self.root.after(30 * 60 * 1000, self.start_auto_export)
    # ── Exit handlers ─────────────────────────────────────────────────────────

    def setup_exit_handlers(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        def handle_crash(exc_type, exc_value, exc_traceback):
            # BUG FIX #5: lock prevents double-export when a background thread
            # crashes at the same moment on_close() runs on the main thread.
            with self._shutdown_lock:
                if not self._shutdown_exported:
                    try:
                        DatabaseManager.export_votes_csv(reason="crash")
                    except Exception:
                        pass
                    self._shutdown_exported = True
            sys.__excepthook__(exc_type, exc_value, exc_traceback)

        sys.excepthook = handle_crash

    def on_close(self):
        for job in [self._github_job, self._export_job,
                    self._control_poll_job,
                    self._next_cat_job]:
            if job:
                self.root.after_cancel(job)

        def _shutdown_export():
            with self._shutdown_lock:
                if not self._shutdown_exported:
                    DatabaseManager.export_votes_csv(reason="shutdown")
                    self._shutdown_exported = True

        t = threading.Thread(target=_shutdown_export, daemon=True)
        t.start()
        t.join(timeout=5)  # wait max 5 seconds, then close regardless

        self.root.destroy()
    # ── Vote multiplier ───────────────────────────────────────────────────────

    def show_multiplier_login(self):
        win = CTkToplevel(self.root)
        win.title("Vote Multiplier Access")
        win.resizable(False, False)
        win.update_idletasks()
        x = (win.winfo_screenwidth()  // 2) - 200
        y = (win.winfo_screenheight() // 2) - 175
        win.geometry(f"400x350+{x}+{y}")
        win.grab_set()

        CTkLabel(win, text="Vote Multiplier Control",
                 font=CTkFont(size=20, weight="bold")).pack(pady=20)
        CTkLabel(win, text="Each vote will count as 2 votes",
                 font=CTkFont(size=12), text_color="orange").pack(pady=5)
        CTkLabel(win, text="Enter Password:", font=CTkFont(size=14)).pack(pady=10)

        pw_e = CTkEntry(win, show="*", width=250, height=40, font=CTkFont(size=14))
        pw_e.pack(pady=10)
        pw_e.focus()

        err = CTkLabel(win, text="", text_color="red")
        err.pack(pady=5)

        status_text  = "ACTIVE (10x)" if self.vote_multiplier_active else "INACTIVE (1x)"
        status_color = "green" if self.vote_multiplier_active else "red"
        CTkLabel(win, text=f"Current: {status_text}",
                 font=CTkFont(size=12, weight="bold"),
                 text_color=status_color).pack(pady=10)

        def verify():
            entered = pw_e.get()
            if not entered:
                err.configure(text="Password cannot be empty!")
                return
            if hashlib.sha256(entered.encode()).hexdigest() == DatabaseManager.get_password_hash("multiplier"):
                win.destroy()
                self.toggle_vote_multiplier()
            else:
                err.configure(text="Invalid password!")
                pw_e.delete(0, END)

        pw_e.bind("<Return>", lambda e: verify())
        CTkButton(win, text="Toggle Multiplier", command=verify,
                  width=150, height=40).pack(pady=15)
        CTkButton(win, text="Cancel", command=win.destroy,
                  width=150, height=40).pack(pady=5)

    def toggle_vote_multiplier(self):
        self.vote_multiplier_active = not self.vote_multiplier_active
        self.vote_multiplier = 2 if self.vote_multiplier_active else 1
        if self.vote_multiplier_active:
            self.multiplier_btn.configure(fg_color="#dc2626", hover_color="#b91c1c",
                                          border_color="#dc2626", text="10x ON")
            self.update_status("10x Vote Mode Active", "#dc2626")
            messagebox.showinfo("Vote Multiplier", "Vote multiplier ACTIVATED – each vote counts as 2!")
        else:
            self.multiplier_btn.configure(fg_color="transparent", hover_color="#1e40af",
                                          border_color="white", text="10x Vote")
            self.update_status("Normal Vote Mode", "#059669")
            messagebox.showinfo("Vote Multiplier", "Vote multiplier DEACTIVATED – normal voting restored")

    def get_vote_increment(self):
        return self.vote_multiplier

    # ── UI helpers ────────────────────────────────────────────────────────────

    def update_status(self, message, color="#059669"):
        self.status_right.configure(text=f"Status: {message}", text_color=color)

    def clear_candidate_card(self):
        for w in self.candidate_card.winfo_children():
            w.destroy()

    def show_voting_inactive_message(self):
        self._clear_voting_container()
        self.category_label.configure(text="VOTING NOT ACTIVE")
        self.candidate_card.pack(expand=True, anchor="n", pady=20)
        # Clear any previously added labels inside the card
        for w in self.candidate_card.winfo_children():
            w.destroy()
        CTkLabel(self.candidate_card,
                 text="Voting is currently not active.\nPlease wait for voting to begin.",
                 font=CTkFont(family="Segoe UI", size=18),
                 text_color="#64748b").pack(expand=True)
    def find_image_file(self, base_path):
        """
        BUG FIX #10: only try appending extensions when the path has no
        extension of its own.  Previously a path like "photo.jpg" would be
        tried as "photo.jpg.jpg", "photo.jpg.jpeg", etc. when the file wasn't
        found at its exact location, meaning valid files were missed.
        """
        if not base_path or not base_path.strip():
            return None
        if os.path.exists(base_path):
            return base_path
        # Only probe extension variants when there is no existing extension
        if not os.path.splitext(base_path)[1]:
            for ext in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff'):
                candidate = base_path + ext
                if os.path.exists(candidate):
                    return candidate
        return None

    # ── Voting flow ───────────────────────────────────────────────────────────

    def display_category(self):
        if not DatabaseManager.get_voting_status():
            self.show_voting_inactive_message()
            return
        self._clear_voting_container()
        # Clear any stale content inside candidate_card
        for w in self.candidate_card.winfo_children():
            w.destroy()
        self.category_label.configure(text=f"SELECT {self.categories[self.current_category]}")
        self.update_status("Voting in Progress", "#2563eb")
        self.current_candidates = DatabaseManager.get_candidates_by_category(
            self.categories[self.current_category]
        )
        self.display_all_candidates()

    def display_all_candidates(self):
        self._candidate_images = []

        # Clear and hide the placeholder card
        self.candidate_card.pack_forget()

        if not self.current_candidates:
            no_cand = CTkFrame(self.voting_container, fg_color="transparent")
            no_cand.pack(fill="both", expand=True)
            CTkLabel(no_cand,
                     text="No candidates available\nfor this category",
                     font=CTkFont(family="Segoe UI", size=18),
                     text_color="#64748b").pack(expand=True)
            self._next_cat_job = self.root.after(2000, self.next_category)
            return

        scroll_outer = CTkScrollableFrame(self.voting_container, fg_color="transparent")
        scroll_outer.pack(fill="both", expand=True, anchor="n")

        grid_container = scroll_outer

        num = len(self.current_candidates)
        cols = min(num, 5)

        for col in range(cols):
            grid_container.grid_columnconfigure(col, weight=1)

        for i, (candidate_id, name, image_path, votes) in enumerate(self.current_candidates):
            row = i // cols
            col = i % cols

            tile = CTkFrame(grid_container, width=350, height=450,
                            corner_radius=20,
                            fg_color=("#ffffff", "#f8fafc"),
                            border_width=2, border_color="#e2e8f0")
            tile.grid(row=row, column=col, padx=20, pady=15, sticky="nsew")
            tile.grid_propagate(False)
            grid_container.grid_rowconfigure(row, weight=1)

            photo_cont = CTkFrame(tile, width=280, height=300,
                                  corner_radius=15, fg_color="#f8fafc",
                                  border_width=1, border_color="#e2e8f0")
            photo_cont.pack(pady=(20, 15))
            photo_cont.pack_propagate(False)

            try:
                actual = self.find_image_file(image_path)
                if not actual:
                    raise FileNotFoundError("No image")
                pil = Image.open(actual).resize((250, 280), Image.Resampling.LANCZOS)
                img = CTkImage(light_image=pil, size=(250, 280))
                self._candidate_images.append(img)
                CTkLabel(photo_cont, image=img, text='').pack(expand=True, padx=15, pady=10)
            except Exception as e:
                print(f"Image load error '{image_path}': {e}")
                CTkLabel(photo_cont, text="📷\nPhoto",
                         font=CTkFont(family="Segoe UI", size=24),
                         text_color="#94a3b8").pack(expand=True)

            CTkLabel(tile, text=name.title(),
                     font=CTkFont(family="Segoe UI", size=18, weight="bold"),
                     text_color="#1e293b").pack(pady=(0, 10))

            def make_vote_action(cid, cname, gc=grid_container):
                cat_index = self.current_category

                def vote_action(btn_ref=None):
                    if cat_index in self._voted_categories:
                        return

                    if btn_ref is not None:
                        try:
                            btn_ref.configure(state="disabled")
                        except Exception:
                            pass

                    # Disable all other VOTE buttons in the grid
                    try:
                        tiles = list(gc.winfo_children())
                    except Exception:
                        tiles = []
                    for tile_w in tiles:
                        try:
                            children = list(tile_w.winfo_children())
                        except Exception:
                            continue
                        for child in children:
                            try:
                                if (isinstance(child, CTkButton) and
                                        child.winfo_exists() and
                                        child.cget("text") == "VOTE"):
                                    child.configure(state="disabled")
                            except Exception:
                                pass

                    # ── Record vote in DB ──────────────────────────────────
                    try:
                        increment = self.get_vote_increment()
                        DatabaseManager.increment_candidate_votes(cid, increment)
                    except Exception as e:
                        self._clear_voting_container()
                        self.candidate_card.pack(expand=True, anchor="n", pady=20)
                        messagebox.showerror("Vote Error",
                                             f"Could not record vote: {e}\nPlease try again.")
                        return

                    # ── Mark voted and advance ─────────────────────────────
                    self._voted_categories.add(cat_index)
                    self._clear_voting_container()
                    self.candidate_card.pack(expand=True, anchor="n", pady=20)
                    self._next_cat_job = self.root.after(500, self.next_category)

                return vote_action

            vote_fn = make_vote_action(candidate_id, name)
            btn = CTkButton(tile, text="VOTE",
                            font=CTkFont(family="Segoe UI", size=16, weight="bold"),
                            fg_color="#1e40af", hover_color="#1e3a8a",
                            corner_radius=12, width=150, height=40)
            btn.configure(command=lambda b=btn, fn=vote_fn: fn(btn_ref=b))
            btn.pack(pady=(0, 20))

    def next_category(self):
        self._next_cat_job = None  # job has fired; clear the reference
        self.current_category += 1
        if self.current_category >= len(self.categories):
            self.show_completion_message()
        else:
            self.display_category()

    def show_completion_message(self):
        self._clear_voting_container()
        self.category_label.configure(text="VOTING COMPLETED")
        self.update_status("Vote Submitted Successfully", "#059669")

        completion_frame = CTkFrame(self.voting_container, fg_color="transparent")
        completion_frame.pack(fill="both", expand=True, anchor="n")

        CTkLabel(completion_frame, text="✓",
                 font=CTkFont(family="Segoe UI", size=120, weight="bold"),
                 text_color="#10b981").pack(pady=(60, 10))
        CTkLabel(completion_frame, text="Thank You for Voting!",
                 font=CTkFont(family="Segoe UI", size=36, weight="bold"),
                 text_color="#1e40af").pack(pady=10)
        CTkLabel(completion_frame,
                 text="Your vote has been recorded securely",
                 font=CTkFont(family="Segoe UI", size=18),
                 text_color="#64748b").pack(pady=10)
        CTkButton(completion_frame, text="New Voter",
                  command=self.restart_voting,
                  font=CTkFont(family="Segoe UI", size=18, weight="bold"),
                  fg_color="#1e40af", hover_color="#1e3a8a",
                  corner_radius=12, width=200, height=50).pack(pady=30)

    def restart_voting(self):
        if self._next_cat_job is not None:
            self.root.after_cancel(self._next_cat_job)
            self._next_cat_job = None

        self._clear_voting_container()
        self.current_category = 0
        self._voted_categories = set()  # ← reset for new voter
        self.update_status("Ready", "#059669")
        if DatabaseManager.get_voting_status():
            self.display_category()
        else:
            self.show_voting_inactive_message()

    # ── Control poll ──────────────────────────────────────────────────────────

    def _start_control_poll(self):
        def _poll():
            if not GitHubManager._credentials_configured():
                return
            try:
                self._handle_voting_status_command()
                self._handle_announcement_command()
                self._handle_force_export_command()
            except Exception as e:
                print(f"Control poll error: {e}")

        threading.Thread(target=_poll, daemon=True).start()
        self._control_poll_job = self.root.after(60_000, self._start_control_poll)

    # ── 1. Remote voting start/stop ───────────────────────────────────────────

    def _handle_voting_status_command(self):
        data = GitHubManager.read_control_file("voting_status.json")
        if data is None:
            return
        remote_active = bool(data.get("active", False))

        with self._control_lock:
            current = DatabaseManager.get_voting_status()
            if remote_active != current:
                DatabaseManager.set_voting_status(remote_active)
                self.root.after(0, self.update_voting_access)
                print(f"Remote command: voting {'started' if remote_active else 'stopped'}")

    # ── 2. Announcement banner ────────────────────────────────────────────────

    def _handle_announcement_command(self):
        data = GitHubManager.read_control_file("announcement.json")
        msg = (data or {}).get("message", "").strip()
        if msg == self._announcement_text:
            return
        self._announcement_text = msg
        self.root.after(0, lambda m=msg: self._apply_announcement(m))

    def _apply_announcement(self, message):
        """
        BUG FIX #6: instead of pack(after=status_frame) — which relies on
        widget insertion order and can silently misplace the banner — we use a
        dedicated _banner_slot frame that was packed in the correct position
        during setup_ui().  We simply replace its contents.
        """
        # Destroy any existing banner content inside the slot
        for w in self._banner_slot.winfo_children():
            w.destroy()

        if not message:
            self._banner_slot.pack_propagate(True)
            self._banner_slot.configure(height=0)
            self._announcement_widget = None
            return

        self._banner_slot.configure(height=48)
        self._banner_slot.pack_propagate(False)

        inner = CTkFrame(
            self._banner_slot,
            corner_radius=0,
            fg_color=("#b45309", "#92400e"),
        )
        inner.pack(fill="both", expand=True)

        CTkLabel(
            inner,
            text=f"📢  {message}",
            font=CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color="white",
        ).pack(expand=True)

        self._announcement_widget = inner
        print(f"Announcement displayed: {message}")

    # ── 3. Force export ───────────────────────────────────────────────────────

    def _handle_force_export_command(self):
        data = GitHubManager.read_control_file("force_export.json")
        if data is None:
            return

        cmd_ts = data.get("ts", "")
        if not cmd_ts or cmd_ts == self._last_export_ts:
            return

        nodes = data.get("nodes", "all")
        raw_node = (DatabaseManager.get_setting('node_name') or
                    socket.gethostname()).strip()
        node_name = "".join(c for c in raw_node if c.isalnum() or c in ('-', '_'))

        if nodes != "all" and node_name not in nodes:
            return

        self._last_export_ts = cmd_ts
        print(f"Force export triggered by panel (ts={cmd_ts})")

        def _do_export():
            filepath = DatabaseManager.export_votes_csv(reason="remote_force")
            if filepath:
                GitHubManager.push_csv(filepath)
            else:
                print("Force export failed – push skipped")

        threading.Thread(target=_do_export, daemon=True).start()

    # ── 4. Heartbeat ──────────────────────────────────────────────────────────

    def _start_heartbeat(self):
        def _beat():
            node_name = (DatabaseManager.get_setting('node_name') or
                         socket.gethostname()).strip()
            node_name = "".join(c for c in node_name if c.isalnum() or c in ('-', '_'))
            GitHubManager.write_heartbeat(node_name)

        threading.Thread(target=_beat, daemon=True).start()
        self._heartbeat_job = self.root.after(300_000, self._start_heartbeat)

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ElectionApp()
    app.run()
