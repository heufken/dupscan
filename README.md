<p align="center">
  <h1 align="center">DupScan</h1>
  <p align="center"><b>A desktop duplicate-file finder for Windows</b><br/>
  polished pywebview GUI · zero-dependency CLI</p>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-blue">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D6">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Zero-dependency CLI" src="https://img.shields.io/badge/CLI-zero%20deps-brightgreen">
</p>

---

DupScan compares files **by content** (BLAKE2b), not by name. It scans one or more folders, groups identical files together, and helps you reclaim space safely: the **first folder you pick is never touched**, and everything you delete goes to the **Recycle Bin**, never a permanent erase.

## ✨ Why DupScan

| | |
|---|---|
| 🛡️ **Safe by design** | The keep folder is read-only by rule, and the GUI refuses any deletion it can't prove is safe (you can't trash every copy of a file, or a file outside the scan). |
| ⚡ **Fast** | A 4 KB head-hash pre-bucket means full files are only read when a cheaper pass already flagged them as candidates. |
| 🖼️ **Nice to use** | Dark/light theme, live progress, cancel, file previews (text, ZIP, tar), reveal in Explorer, search, sort, and one-click auto-pick of which copies to keep. |
| 💻 **Also a CLI** | `dupscan.py` runs headless with no dependencies at all. |

## 🚀 Getting started

Requires **Python 3.11+** (uses `hashlib.file_digest`) on Windows.

```bash
pip install pywebview send2trash
python dupscan.py --gui
```

Or just double-click **`start.bat`**.

### CLI (no dependencies)

```bash
python dupscan.py D:/Photos D:/Backup            # list duplicate groups
python dupscan.py D:/Photos D:/Backup --delete   # trash extra copies (first folder is safe)
```

Useful? Star the repo ★. Found a bug? Open an issue.

## 🔍 How it works

1. **Walk** every folder, grouping files by size (empty files are skipped).
2. **Head-hash** — hash the first 4 KB of every file in a size-collision group.
3. **Verify** — for groups larger than 4 KB that survive the head-hash, re-hash the full body. Only full-content matches are reported.

Each duplicate group is sorted so the copy in your keep folder (or the earliest folder listed) comes first. Deletions are reversible via the Recycle Bin.

## 📁 Project layout

| File | Purpose |
|------|---------|
| `dupscan.py` | All logic: scanning, hashing, CLI, pywebview bridge API |
| `index.html` | The GUI — a single-page app rendered inside the pywebview window |
| `design/tokens.css` | Design tokens and component styles for the UI |
| `start.bat` | One-click launcher for the GUI on Windows |

## 📜 License

MIT