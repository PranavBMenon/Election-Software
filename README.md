# BVBVM Election Management System

A school election system made of two parts:

1. **A desktop voting app** (`test.py`), built with Python, CustomTkinter and SQLite. Voters pick candidates on a touch-friendly screen, and an admin panel handles candidates, statistics, backups and optional GitHub syncing.
2. **A web admin dashboard** (`index.html`), a single static page that reads the CSVs pushed to GitHub, shows combined results from all voting machines, controls them remotely, and runs a winner-reveal presentation.

The default categories are **Head Boy**, **Head Girl**, **Assistant Head Boy** and **Assistant Head Girl**.

> ## ⚠️ Project status: under development, currently paused
>
> This project is **still in development** and is **not production-ready**. I've lost interest in it for now, so it isn't being actively worked on, but I plan to return to it at some point in the future.
>
> Expect **bugs, rough edges and unfinished features**. Some known problems are listed under [Known issues](#known-issues), but there are likely others that haven't been found yet. Test thoroughly before relying on it for a real election, and keep the CSV backups.

---

## Features

### Desktop voting app (`test.py`)

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

### Web admin dashboard (`index.html`)

- **Password gate** with a 5-attempt limit (see [Known issues](#known-issues) for why this is not real security)
- **Auto-discovery of CSVs** in the `votes/` folder of the repository through the GitHub API; the repository is detected from a `*.github.io` URL or typed into the toolbar
- **Summary cards**: total votes, nodes loaded, categories and candidates
- **Results by category** with ranked bars, a "Leading" badge and an expandable per-node vote breakdown
- **Node status cards** showing whether each node's file is active, empty or failed, and its last export time
- **Vote trend chart** per category (Chart.js), drawn from the export timestamps
- **Duplicate-name detection** and **Merge Duplicate Spellings**, plus a **Manual Name Mapping** tool for typos. Merges are stored in the browser's `localStorage` and never change the original CSVs
- **Remote Control panel** that writes to the repository's `control/` folder:
  - start / stop voting
  - push or clear an announcement banner
  - force an export on selected nodes
  - node online status from heartbeat files
- **Winner reveal**: a full-screen countdown and winner screen per category (or for all categories), with optional candidate photos and confetti
- **Auto-refresh** every 30 s, 60 s, 2 min or 5 min

The remote-control features need a GitHub token with write access. The dashboard asks for it once per session and keeps it only in memory.

---

## Requirements

**Desktop app**
- Python 3.9 or newer
- Tkinter (included with most Python installers)

```bash
pip install customtkinter pillow matplotlib
pip install PyGithub        # optional, only needed for GitHub sync
```

If PyGithub isn't installed, the app still runs and the GitHub features are disabled.

**Web dashboard**
- A modern browser and an internet connection (it loads Chart.js and Google Fonts from CDNs)
- A GitHub repository that the voting machines push to. Host the page with GitHub Pages, or open it locally and type `owner/repo-name` into the toolbar
- A **public** repository is needed for the dashboard to read CSVs without a token (see [Known issues](#known-issues))

---

## Running

### Desktop app

```bash
python test.py
```

On first launch the app creates its database and default settings automatically. No database server is needed.

Optional: place `bvblogo2.png` in the same folder as the script to use it as the window icon.

### Dashboard

Open `index.html` in a browser (or from GitHub Pages), enter the dashboard password, confirm the repository in the toolbar, and click **Refresh Votes**.

---

## File locations

| What | Where |
|---|---|
| Database | `~/Desktop/election.db` |
| CSV backups | `~/Desktop/ElectionBackups/` |
| Backup filename format | `<node>_votes_<reason>_<timestamp>.csv` |

`<reason>` is one of `auto`, `manual`, `shutdown`, `crash`, `github_push` or `remote_force`.

Repository layout used by GitHub sync:

| Folder | Written by | Contents |
|---|---|---|
| `votes/` | Voting app | Vote CSVs from each node |
| `control/` | Dashboard | `voting_status.json`, `announcement.json`, `force_export.json` |
| `heartbeat/` | Voting app (disabled) | One `<node>.json` per node |

---

## First-time setup

1. Run the app and click **Admin** (top right).
2. Log in with the default password `admin123`.
3. Open **Change Password** and change **both** the Admin and Vote Multiplier passwords. Both start as `admin123`.
4. Click **Add Candidate** for each candidate. For photos, use **Browse** to pick an image; the full path is stored, so keep the image files in place.
5. (Optional) Set up GitHub sync, described below.
6. Click **Start Voting**.
7. (Optional) Change `ADMIN_PASSWORD` at the top of the script in `index.html` before publishing the dashboard. The default is `admin2025`.

---

## Running an election

1. An admin starts voting from the admin panel (or remotely from the dashboard).
2. A voter selects one candidate in each category. Each vote is saved immediately.
3. After the last category the "Thank You" screen appears. Click **New Voter** for the next person.
4. When finished, an admin clicks **Stop Voting** and uses **View Statistics** or **Export Votes Now** to get results.
5. Open the dashboard to see the combined results from all nodes, fix any duplicate spellings, and run the **Reveal All Winners** presentation.

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

> **Repository visibility.** The CSVs contain live vote counts, so a private repository is safer. But the dashboard reads CSVs without a token, so it only works with a public repository as written. See [Known issues](#known-issues).

### Remote control files

Every 60 seconds the app checks the `control/` folder of the repository for these files:

| File | Example content | Effect |
|---|---|---|
| `voting_status.json` | `{"active": true}` | Starts or stops voting |
| `announcement.json` | `{"message": "Polls close at 3 PM"}` | Shows a banner (empty message hides it) |
| `force_export.json` | `{"ts": "2025-01-01T10:00", "nodes": "all"}` | Exports and pushes a CSV. `nodes` can be `"all"` or a list of node names. Change `ts` to trigger it again |

The dashboard writes these files for you from its **Remote Control** panel.

A heartbeat feature exists in the code but is disabled (the call in `ElectionApp.__init__` is commented out). The dashboard expects heartbeat files in `heartbeat/<node>.json` and treats a node as online if it was seen in the last 3 minutes.

---

## Project structure

| File | Purpose |
|---|---|
| `test.py` | The desktop voting app (SQLite version) |
| `index.html` | The web admin dashboard, with its HTML, CSS and JavaScript in one file |
| `README.md` | This file |

`test.py` is made up of these parts:

| Component | Purpose |
|---|---|
| `db_execute` / settings cache | Thread-safe SQLite access and cached settings |
| `DatabaseManager` | Tables, passwords, candidates, votes, statistics, CSV export |
| `GitHubManager` | Pushing CSVs, reading control files, writing heartbeats |
| `AdminPanel` | Admin login and all admin windows |
| `ElectionApp` | Main window, voting flow, schedulers, crash and shutdown handling |

Database tables: `app_passwords`, `candidates`, `voting_status`, `app_settings`.

---

## Customising

- **Categories** are hardcoded in two places: `AdminPanel.categories` and `ElectionApp.categories`. Keep both lists identical. The dashboard reads categories from the CSVs, and guesses each one's role (boy, girl, assistant) from its name for the reveal screen.
- **Title and branding** are set in `ElectionApp.setup_ui` and `__init__`.
- **Database path** is set by `_DB_PATH` near the top of the file.
- **Auto-export interval** is set in `start_auto_export` (currently 30 minutes).
- **Dashboard password** is `ADMIN_PASSWORD` in the script section of `index.html`.
- **Reveal countdown length** is the `runCountdown(10, ...)` call in `index.html`.

---

## Known issues

### Bugs

- **The dashboard probably overcounts votes.** Each push creates a new timestamped CSV (`<node>_votes_<reason>_<timestamp>.csv`). The dashboard loads every CSV in `votes/` and adds up all rows, so repeated snapshots from the same node are counted again and again. It should use only the latest file per node.
- **Node names are read from the filename.** The dashboard's `nodeNameFromPath` only recognises names like `Node_3`. For a name like `Lab-PC-03`, each snapshot file is treated as a separate node, which also breaks the per-node breakdown and the force-export node list.
- **The Detailed View tab crashes.** SQLite returns `created_at` as text, but the code calls `.strftime()` on it. Using `created_at[:16]` instead would fix it.
- **The vote multiplier is mislabelled.** The button and some messages say "10x", but it actually counts each vote as 2.
- **The dashboard says nodes react within 30 s**, but the app polls the `control/` folder every 60 s.
- **The "Node online status" card always shows "no heartbeat"**, because the heartbeat is disabled in the app.
- **The startup error message is out of date.** If the database fails to open, the app says "make sure MySQL is running", but this version uses SQLite.

### Security and integrity

- **The dashboard password is not real protection.** `ADMIN_PASSWORD` is in the page's JavaScript, so anyone who views the source can read it. Anyone who can reach the page can also read the votes directly from the repository.
- **The dashboard needs a public repository** to read CSVs without a token, which conflicts with keeping live vote counts private.
- **The multiplier is an integrity risk.** Anyone with the multiplier password can inflate vote counts. Consider removing the feature for real elections.
- **No voter identity tracking.** The one-vote-per-category rule applies only within a session, so nothing prevents someone from using "New Voter" to vote again.
- **Weak password storage.** Passwords are hashed with unsalted SHA-256, and the default is `admin123`.
- **The GitHub token is stored in plaintext** in the local database.

---

## Related versions

- `server.py`: the same app using MySQL instead of SQLite
- `testv2.py`: replaces GitHub sync with a LAN sync to an admin PC and removes the remote-control features (the dashboard's remote-control features depend on the GitHub version)
