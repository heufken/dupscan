"""Find duplicate files by content (BLAKE2b hash) across one or more directories.

Folder order matters: in each group the file kept is the one in the earliest folder listed.
With two or more folders, the first one is read-only: nothing in it is ever deleted.
Deleted files go to the Recycle Bin (pip install send2trash).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import islice
from pathlib import Path


def hash_file(path, limit=0):
    with open(path, "rb") as f:
        if limit:
            return hashlib.blake2b(f.read(limit)).hexdigest()
        return hashlib.file_digest(f, "blake2b").hexdigest()  # Python 3.11+


def root_of(p, roots):
    """Index of the first scanned folder containing p."""
    return next(i for i, r in enumerate(roots) if Path(p).is_relative_to(os.path.abspath(r)))


def find_duplicates(*roots, progress=None, recursive=True, min_size=1):
    """Return [(size, [paths])], largest size first; each group sorted by (folder order, path)."""
    by_size = defaultdict(set)  # set: overlapping folders must not list a file twice
    n_seen = 0
    for root in roots:
        for dirpath, dirs, names in os.walk(os.path.abspath(root)):
            if not recursive:
                dirs.clear()  # top level only
            for name in names:
                p = os.path.join(dirpath, name)
                n_seen += 1
                if progress and n_seen % 200 == 0:
                    progress("walking", n_seen)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue  # unreadable/symlink target gone
                if size >= min_size:  # default 1: empty files all hash alike, never useful
                    by_size[size].add(p)

    n_hash = 0
    n_hash_total = sum(len(p) for p in by_size.values() if len(p) > 1)
    n_verify = 0
    dups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_head = defaultdict(list)
        for p in paths:  # cheap 4 KB pre-hash buckets same-size files before any full read
            n_hash += 1
            if progress:
                progress("hashing", (n_hash, n_hash_total))
            try:
                by_head[hash_file(p, 4096)].append(p)
            except OSError:
                pass
        buckets = by_head.values()
        if size > 4096:  # head match is not proof: re-hash full bodies of candidates only
            buckets = []
            for head in by_head.values():
                if len(head) < 2:
                    continue
                by_hash = defaultdict(list)
                for p in head:
                    n_verify += 1
                    if progress:
                        progress("verify", n_verify)
                    try:
                        by_hash[hash_file(p)].append(p)
                    except OSError:
                        pass
                buckets += by_hash.values()
        dups += [(size, sorted(g, key=lambda p: (root_of(p, roots), p)))
                 for g in buckets if len(g) > 1]
    return sorted(dups, key=lambda d: -d[0])


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class Group:
    size: int  # per-file size in bytes
    paths: list


def is_locked(p, roots):
    """With 2+ folders the first is the reference/backup side: never deletable."""
    return len(roots) > 1 and root_of(p, roots) == 0


def check_delete(groups, paths, locked):
    """Return an error string if trashing `paths` is unsafe, else None."""
    owner = {p: g for g in groups for p in g.paths}
    gone = Counter()
    for p in set(paths):
        if p not in owner:
            return f"Not in scan results: {p}"
        if p in locked:
            return f"Protected (keep folder): {p}"
        gone[id(owner[p])] += 1
    if any(gone[id(g)] >= len(g.paths) for g in groups):
        return "Refusing to delete every copy in a group"
    return None


PEEK_LINES, PEEK_ENTRIES = 40, 60
TAR_EXT = {".tar", ".gz", ".tgz", ".bz2", ".xz"}


def peek(path):
    """Cheap content preview: zip/tar -> {kind:'list', items, more}; text -> {kind:'text', body, more}; else None."""
    ext = Path(path).suffix.lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            return {"kind": "list", "items": names[:PEEK_ENTRIES], "more": len(names) > PEEK_ENTRIES}
        if ext in TAR_EXT:
            try:
                with tarfile.open(path) as t:
                    names = [m.name for m in islice(t, PEEK_ENTRIES + 1)]
                return {"kind": "list", "items": names[:PEEK_ENTRIES], "more": len(names) > PEEK_ENTRIES}
            except Exception:
                pass  # e.g. a plain .gz: fall through to the text sniff
        with open(path, "rb") as f:
            head = f.read(8192)
    except Exception:
        return None  # a preview must never raise
    if b"\0" in head:
        return None  # binary
    lines = head.decode("utf-8", errors="replace").splitlines()
    return {"kind": "text", "body": "\n".join(lines[:PEEK_LINES]),
            "more": len(lines) > PEEK_LINES or len(head) == 8192}


def reveal(path):
    """Open Explorer with `path` selected."""
    # ponytail: Windows only; add `open -R` / xdg-open if you move platforms
    if sys.platform == "win32":
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')


class Cancelled(Exception):
    pass


SETTINGS = Path.home() / ".dupscan.json"


def trash_files(paths):
    """Send files to the Recycle Bin (reversible)."""
    from send2trash import send2trash  # lazy: CLI scanning works without it
    for p in paths:
        send2trash(p)


class Api:
    """pywebview bridge. Scan state: '' | 'scanning' | 'done'."""

    def __init__(self):
        self._window = None  # private: pywebview's API injector skips underscore attrs
        self._groups = []
        self._roots = []
        self._locked = set()
        self._state = ""
        self._cancel = False

    def _js(self, event, data):
        if self._window:
            self._window.evaluate_js(f"app.event({json.dumps(event)}, {json.dumps(data)})")

    def on_progress(self, phase, val):
        if self._cancel:
            raise Cancelled  # ponytail: checked per file; one in-flight huge file still delays it
        if phase == "hashing":
            n, total = val
            if n % 10:
                return  # cancel check above already ran; UI only every 10th
            self._js("progress", {"pct": n / max(total, 1) * 100, "label": f"{n}/{total} files hashed"})
        elif phase == "verify":
            if val % 10:
                return
            self._js("progress", {"pct": None, "label": f"{val:,} full copies verified"})
        else:
            self._js("progress", {"pct": None, "label": f"{val:,} files found"})

    def _payload(self, g):
        files = []
        for p in g.paths:
            ri = root_of(p, self._roots)
            sub = Path(p).parent.relative_to(os.path.abspath(self._roots[ri])).as_posix()
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                mtime = 0
            files.append({"path": p, "url": Path(p).as_uri(), "root": ri, "mtime": mtime,
                          "sub": "" if sub == "." else sub, "locked": p in self._locked})
        return {"size": g.size, "files": files}

    def pick_folder(self):
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None

    def cancel(self):
        self._cancel = True

    def scan(self, roots, recursive=True, min_kb=0):
        if self._state == "scanning":
            return {"error": "Scan already running"}
        self._state = "scanning"
        self._cancel = False
        try:
            groups = [Group(size, paths) for size, paths in find_duplicates(
                *roots, progress=self.on_progress, recursive=recursive, min_size=max(1, int(min_kb * 1024)))]
            self._roots, self._groups = list(roots), groups
            self._locked = {p for g in groups for p in g.paths if is_locked(p, roots)}
            self._state = "done"
            return {"groups": [self._payload(g) for g in groups]}
        except Cancelled:
            self._state = ""
            return {"cancelled": True}
        except Exception as e:
            self._state = ""
            return {"error": str(e)}

    def _known(self, path):  # trust boundary: the page may only touch files from the current scan
        return any(path in g.paths for g in self._groups)

    def peek(self, path):
        """Preview text/archive contents."""
        return peek(path) if self._known(path) else None

    def reveal(self, path):
        if self._known(path):
            reveal(path)

    def open_file(self, path):
        """Open with the default application."""
        if self._known(path) and sys.platform == "win32":
            os.startfile(path)

    def load_settings(self):
        try:
            s = json.loads(SETTINGS.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(s, dict):
            return {}
        s["slots"] = [d if d and os.path.isdir(d) else None for d in s.get("slots", [])]  # drop vanished folders
        return s

    def save_settings(self, s):
        try:
            SETTINGS.write_text(json.dumps(s))
        except OSError:
            pass

    def trash(self, paths):
        """Recycle `paths`. Returns {removed: [...], error: str|None}; partial success is reported."""
        err = check_delete(self._groups, paths, self._locked)
        done = []
        if not err:
            try:
                for p in paths:
                    trash_files([p])
                    done.append(p)
            except Exception as e:
                err = str(e)
        gone = set(done)
        for g in self._groups:
            g.paths = [p for p in g.paths if p not in gone]
        self._groups = [g for g in self._groups if len(g.paths) > 1]
        return {"removed": done, "error": err}

    def quit(self):
        self._window.destroy()


def gui():
    globals()['webview'] = __import__('webview')  # CLI stays importable without pywebview
    api = Api()
    page = (Path(__file__).parent / "index.html").as_uri()
    webview.create_window("DupScan", page, js_api=api,
                          width=1100, height=720, min_size=(760, 480))
    api._window = webview.windows[0]
    webview.start()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="*", help="directories to scan; the first one listed keeps its copies")
    ap.add_argument("--delete", action="store_true",
                    help="move all but the first file of each group to the Recycle Bin "
                         "(with 2+ folders, files in the first folder are never touched)")
    ap.add_argument("--gui", action="store_true", help="launch the pywebview GUI instead of CLI output")
    args = ap.parse_args()
    if args.gui:
        try:
            import webview
        except ImportError:
            webview = None
        if webview is None:
            ap.error("--gui requires pywebview and send2trash: pip install pywebview send2trash")
        gui()
        return
    if not args.root:
        ap.error("the following arguments are required: root")

    dup_bytes = 0
    groups = find_duplicates(*args.root)
    for size, paths in groups:
        print(f"\n{paths[0]}")
        dup_bytes += size * (len(paths) - 1)
        for p in paths[1:]:
            print(p)
        if args.delete:
            trash_files([p for p in paths[1:] if not is_locked(p, args.root)])
    print(f"\n{len(groups)} duplicate group(s), {human(dup_bytes)} wasted"
          + (" — moved to Recycle Bin." if args.delete else ""))


if __name__ == "__main__":
    main()