# VendLog

> **⚠️ AI-generated software.** This entire project (all source code, tests, and
> documentation, including this README) was written by an AI assistant (GitHub Copilot) at
> the request of the repository owner. It has been tested only lightly. Review it before
> relying on it, and keep backups of your data.

A small, self-hosted web app for cataloguing, tagging, searching and previewing
vending machine log files. Companion to VendTerm. Built for two people sharing a
private GitHub repository; no database server, no cloud service.

- **Manage logs**: upload, rename, move, delete, tag, and edit details.
- **Search & view logs**: filename, tag and full-text search, with a paged preview.
- **Synchronise**: pull, commit and push through your own local Git.

## Two repositories

| Repository | Contents | Local folder |
|---|---|---|
| `VendLog_App` | this application | the folder containing `app.py` |
| `VendLog_Data` | log files and metadata | `logs\` inside the app folder |

`logs\` is its own Git clone and is ignored by the app repository. The app
never handles passwords or tokens; see [Signing in](#signing-in-to-github).

## Requirements

- Windows or macOS, Python 3.9+ and Git (with Git Credential Manager, included in Git for Windows and the
  Git installer for macOS from git-scm.com; Xcode's Git works too, with the macOS keychain).
- Flask (installed from `requirements.txt`).

## Setup

```
git clone https://github.com/epremeaux/VendLog_App.git
cd VendLog_App
pip install -r requirements.txt
```

Start it with the launcher:

- **Windows**: double-click **`VendLog.bat`**.
- **macOS**: double-click **`VendLog.command`**. The first time, macOS may block it: right-click it,
  choose **Open**, then **Open** again. If it says permission denied, run `chmod +x VendLog.command`
  once in Terminal. The first run creates a private `.venv` and installs Flask.

The launcher starts the server and opens
<http://localhost:8765>. If VendLog is already running it only opens the
browser. Or run `python app.py` (add `--no-browser` to skip opening the browser).

Then open **Synchronise** and connect the data repository. If you already have a
clone of the data repo, put it at `logs\` and it is used as is.

### Configuration (optional)

Copy `vendlog.config.example.json` to `vendlog.config.json` (not committed):

| Key | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Keep as is; the app has no login and is for this PC only. |
| `port` | `8765` | Change if the port is taken. |
| `logs_dir` | `logs` | Where the data repo lives. |
| `data_repo_url` | the VendLog_Data URL | Default repository offered on the Synchronise page. |
| `external_editor` | empty | Program used by the viewer's "Open in external editor" button, e.g. `C:\Program Files\Notepad++\notepad++.exe`. Usually set on the Home page. Local to this computer. |
| `auto_pull_on_start` | `false` | If `true`, the first page load after startup pulls GitHub changes, but only as a clean fast-forward with no local changes. Otherwise it just shows a banner. Never merges and never pushes. |

### Banners
Every page checks GitHub quietly (at most once a minute) and shows a banner when you have changes
that are not pushed yet, when GitHub has new commits, or when a newer app is available.
Push before closing VendLog so your colleague gets your work.

## Using it

### Manage logs
- **Upload**: choose files, optionally set machine, maker, date, tags and a filename prefix.
  Files are stored in `logs\<machine>\`. Existing files are never overwritten.
- **Machine** names also select the folder. Near-duplicates (e.g. `Coke Red` vs `coke_red`) are flagged.
- **Machine maker**: Fuji Denki, Sanden, Panasonic, JVMA, UNKNOWN, or type a new one.
- Select a file to edit its tags, machine, maker, date and description. Changing the
  machine moves the file. Tick several files to bulk tag or bulk move them.
- **Tags** are a shared vocabulary with autocomplete. Rename or delete a tag globally from the tag manager.
- Deleting asks for confirmation.

### Search & view logs
- Type words to match filenames, tags, machine, maker, description and (optionally) file contents.
- Search is case-insensitive. Use **All** (AND) or **Any** (OR); comma-separated terms such as
  `Keyboard entry, JVMA` match files having both.
- Click a result to preview it. Text files are paged so large files are not loaded whole.
  Images (png, jpg, gif, webp, bmp, svg) are shown directly, with Fit to window, Actual size and open-in-new-tab.
  Use the find box with prev/next, or go to a line. Text encodings utf-8, utf-16 and cp1252 are detected.

### Synchronise
1. **Check GitHub for changes** only looks and updates the status. No files change.
2. **Pull logs & metadata** downloads your partner's changes. If the same file changed
   on both sides, nothing is changed and you are told which files clash.
3. **Commit & push logs** commits everything under `logs\` and pushes. It never forces,
   and refuses if GitHub is ahead; pull first.
4. **Change repository** is locked behind an unlock box, then two steps (check, then type the
   repo name). Your old `logs\` folder is kept as `logs.old-<timestamp>`.

Daily routine: **Pull**, work, **Commit & push**.

### Versions and updating
The Synchronise page shows **App version** and **Data version**. Every push records the
newest app version that touched the data in `vendlog.json`. If your partner's data came from a
newer app you get a warning; use **Check for app updates**, then **Update app from GitHub**,
and restart VendLog. When you change the app, raise `__version__` in `vendlog\__init__.py`
before pushing so others are notified. A data format newer than your app supports blocks pushing.

## Signing in to GitHub

VendLog runs the local `git` command, so Git Credential Manager handles sign-in. The first
clone, pull or push opens a browser sign-in once and remembers it. VendLog never asks for or stores
credentials, and rejects URLs that contain a password or token. The GitHub account needs
access to the data repository (add it as a collaborator).

## Data format

Each log `name.log` has a sidecar `name.log.meta.json` next to it:

```json
{
  "tags": ["JVMA", "Door open"],
  "machine": "Coke Red Lab",
  "maker": "Fuji Denki",
  "datetime": "2026-03-14T09:30",
  "description": "Door switch test"
}
```

`tags.json` holds the tag vocabulary and `vendlog.json` the version stamp. All are plain,
sorted JSON that diff and merge cleanly. An example is in [`docs\example`](docs/example).

## Large files

GitHub rejects files over 100 MB and warns above 50 MB. Split or compress very large logs.

## Tests

```
pip install pytest
python -m pytest -q
```

## Troubleshooting

- **Port in use**: another program holds 8765. Set `port` in `vendlog.config.json`.
- **"unable to write new index file" / access denied**: Dropbox briefly locked the folder; retry.
  Keeping the app folder outside Dropbox avoids this.
- **Sign-in prompt never appears**: run `git ls-remote <repo url>` once in a terminal to sign in.
