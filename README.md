# BVBVM Election Management System

A desktop voting application for school elections, built with Python, CustomTkinter and SQLite. Voters pick candidates on a touch-friendly screen, and an admin panel handles candidates, statistics, backups and optional GitHub syncing.

The default categories are **Head Boy**, **Head Girl**, **Assistant Head Boy** and **Assistant Head Girl**.

> ## ⚠️ Project status: under development, currently paused
>
> This project is **still in development** and is **not production-ready**. I've lost interest in it for now, so it isn't being actively worked on, but I plan to return to it at some point in the future.
>
> Expect **bugs, rough edges and unfinished features**. Some known problems are listed under [Known issues](#known-issues), but there are likely others that haven't been found yet. Test thoroughly before relying on it for a real election, and keep the CSV backups.

---

## Features

**Voting**
- Full-screen candidate grid with photo, name and a VOTE button
- Steps through each category in order, then shows a "Thank You" screen
- One vote per category per voter session; the "New Voter" button resets the flow
- Voting only works while an admin has started it

**Admin panel** (password protected)
- Start / stop voting
- Add candidates (name, category, optional photo) and delete them
- Reset all votes
- Statistics with three tabs: Summary, Detailed View and Graphs (bar, pie and comparison charts)
- Manual CSV export
- Change the admin and multiplier passwords
- GitHub settings

**Backups and sync**
- Automatic CSV export every 30 minutes, on shutdown, and on crash
- Optional scheduled push of CSVs to a GitHub repository
- Optional remote control through JSON files in the same repository (start/stop voting, announcement banner, force export)

---

## Requirements

- Python 3.9 or newer
- Tkinter (included with most Python installers)

Python packages:

```bash
pip install customtkinter pillow matplotlib
pip install PyGithub        # optional, only needed for GitHub sync
```

If PyGithub isn't installed, the app still runs and the GitHub features are disabled.

---

## Running

```bash
python test.py
```

On first launch the app creates its database and default settings automatically. No database server is needed.

Optional: place `bvblogo2.png` in the same folder as the script to use it as the window icon.

---

## File locations

| What | Where |
|---|---|
| Database | `~/Desktop/election.db` |
| CSV backups | `~/Desktop/ElectionBackups/` |
| Backup filename format | `<node>_votes_<reason>_<timestamp>.csv` |

`<reason>` is one of `auto`, `manual`, `shutdown`, `crash`, `github_push` or `remote_force`.

---

## First-time setup

1. Run the app and click **Admin** (top right).
2. Log in with the default password `admin123`.
3. Open **Change Password** and change **both** the Admin and Vote Multiplier passwords. Both start as `admin123`.
4. Click **Add Candidate** for each candidate. For photos, use **Browse** to pick an image; the full path is stored, so keep the image files in place.
5. (Optional) Set up GitHub sync, described below.
6. Click **Start Voting**.

---

## Running an election

1. An admin starts voting from the admin panel.
2. A voter selects one candidate in each category. Each vote is saved immediately.
3. After the last category the "Thank You" screen appears. Click **New Voter** for the next person.
4. When finished, an admin clicks **Stop Voting** and uses **View Statistics** or **Export Votes Now** to get results.

---

## GitHub sync (optional)

Open **Admin > GitHub Settings** and fill in:

| Field | Description |
|---|---|
| Personal Access Token | GitHub token with permission to write repository contents |
| Node Name | Label for this machine, e.g. `Lab-PC-03` (falls back to the computer's hostname) |
| GitHub Username | Account that owns the repository |
| Repository Name | An existing repository |
| Push Interval | Minutes between automatic pushes (default 60) |

Use **Push Now** to test. The status of the last push is shown in the same window.

CSVs are uploaded to the `votes/` folder of the repository.

> **Use a private repository.** The CSVs contain live vote counts.

### Remote control files

Every 60 seconds the app checks the `control/` folder of the repository for these files:

| File | Example content | Effect |
|---|---|---|
| `voting_status.json` | `{"active": true}` | Starts or stops voting |
| `announcement.json` | `{"message": "Polls close at 3 PM"}` | Shows a banner (empty message hides it) |
| `force_export.json` | `{"ts": "2025-01-01T10:00", "nodes": "all"}` | Exports and pushes a CSV. `nodes` can be `"all"` or a list of node names. Change `ts` to trigger it again |

A heartbeat feature exists in the code but is disabled.

---

## Project structure

`test.py` is a single file made up of these parts:

| Component | Purpose |
|---|---|
| `db_execute` / settings cache | Thread-safe SQLite access and cached settings |
| `DatabaseManager` | Tables, passwords, candidates, votes, statistics, CSV export |
| `GitHubManager` | Pushing CSVs and reading control files |
| `AdminPanel` | Admin login and all admin windows |
| `ElectionApp` | Main window, voting flow, schedulers, crash and shutdown handling |

Database tables: `app_passwords`, `candidates`, `voting_status`, `app_settings`.

---

## Customising

- **Categories** are hardcoded in two places: `AdminPanel.categories` and `ElectionApp.categories`. Keep both lists identical.
- **Title and branding** are set in `ElectionApp.setup_ui` and `__init__`.
- **Database path** is set by `_DB_PATH` near the top of the file.
- **Auto-export interval** is set in `start_auto_export` (currently 30 minutes).

---

## Known issues

- **The Detailed View tab crashes.** SQLite returns `created_at` as text, but the code calls `.strftime()` on it.
- **The vote multiplier is mislabelled.** The button and some messages say "10x", but it actually counts each vote as 2.
- **The multiplier is an integrity risk.** Anyone with the multiplier password can inflate vote counts. Consider removing the feature for real elections.
- **No voter identity tracking.** The one-vote-per-category rule applies only within a session, so nothing prevents someone from using "New Voter" to vote again.
- **Weak password storage.** Passwords are hashed with unsalted SHA-256, and the default is `admin123`.
- **The GitHub token is stored in plaintext** in the local database.

---

## Related versions

- `server.py`: the same app using MySQL instead of SQLite
- `testv2.py`: replaces GitHub sync with a LAN sync to an admin PC and removes the remote-control features
