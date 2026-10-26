#!/usr/bin/env python3
"""
OpenCode Prompt Manager
-----------------------
Read, dump, replace, clear, and restore the system prompts embedded in the
OpenCode executable.

Run it by double-clicking this file, or:  python ocprompt.pyw

Every change saves a full copy of the binary plus a text dump of all prompts
under backups/ before it touches anything. If an opencode.exe sits next to
this file, that copy is used by default, so experiments never touch the
installed one.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
BACKUP_DIR = APP_DIR / "backups"
STATE_FILE = APP_DIR / "regions.json"
LOCAL_BIN = APP_DIR / "opencode.exe"

INSTALLED_BIN = (
    Path(os.environ.get("APPDATA", ""))
    / "npm" / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe"
)

# ------------------------------------------------------------------ scanning

UTF8_ANCHORS = (b"You are ", b"# Harness", b"# Code comments")
UTF16_ANCHOR = b"Y\x00o\x00u\x00 \x00a\x00r\x00e\x00 \x00"

MAX_REGION = 1_000_000
MIN_REGION = 120

FRIENDLY = [
    ("main", "Main system prompt"),
    ("main-coding", "Coding agent"),
    ("main-cli", "CLI agent"),
    ("main-general", "General agent"),
    ("main-shared", "Shared workspace agent"),
    ("main-harness", "Harness prompt"),
    ("main-opencode", "OpenCode prompt"),
    ("explore", "File search agent"),
    ("review", "Code reviewer"),
    ("title", "Title generator"),
    ("summary", "Conversation summarizer"),
    ("style", "Code style rules"),
    ("template", "Prompt template"),
    ("agent-architect", "Agent architect"),
    ("coding-agent", "Coding agent (legacy)"),
]


def default_binary():
    return LOCAL_BIN if LOCAL_BIN.exists() else INSTALLED_BIN


def friendly_name(role):
    base, _, suffix = role.partition("#")
    name = dict(FRIENDLY).get(base, "Prompt")
    if suffix.isdigit() and int(suffix) > 1:
        name += f"  (copy {suffix})"
    return name


def type_of(role):
    base = role.partition("#")[0]
    if base.startswith("main"):
        return "System prompt"
    if base in ("explore", "review", "title", "summary", "agent-architect"):
        return "Agent"
    if base == "style":
        return "Style rules"
    return "Prompt"


# Model families, in the order OpenCode picks them. Each entry maps to the
# bunfs stem the prompt is shipped under, so the path is resolved from the
# binary at runtime instead of hardcoding a build hash.
MODEL_FAMILIES = [
    {
        "stem": "system",
        "name": "Default fallback",
        "models": "DeepSeek, Qwen, Grok, GLM, MiniMax, Llama, anything unlisted",
        "test": lambda t: (t.lstrip().startswith("You are an AI agent running in OpenCode")
                           and len(t) < 1200),
    },
    {
        "stem": "gpt",
        "name": "GPT family",
        "models": "GPT-4.x, GPT-5.x, o1 / o3",
        "test": lambda t: (t.lstrip().startswith("You are an AI agent running in OpenCode")
                           and 1200 <= len(t) < 3200),
    },
    {
        "stem": "gpt-astra",
        "name": "GPT-Astra",
        "models": "Astra variants",
        "test": lambda t: (t.lstrip().startswith("You are an AI agent running in OpenCode")
                           and len(t) >= 3200),
    },
    {
        "stem": "kimi",
        "name": "Kimi / Moonshot",
        "models": "Kimi K2, K2.5, Moonshot",
        "test": lambda t: t.lstrip().lower().startswith(
            "you are opencode, an interactive general ai agent"),
    },
    {
        "stem": "meta",
        "name": "Meta",
        "models": "Muse Spark, Meta MSL models",
        "test": lambda t: t.lstrip().lower().startswith(
            "you are opencode, a coding agent"),
    },
    {
        "stem": "trinity",
        "name": "Trinity",
        "models": "Trinity, Big Pickle",
        "test": lambda t: t.lstrip().lower().startswith(
            "you are opencode, an interactive cli tool"),
    },
    {
        "stem": "anthropic",
        "name": "Claude",
        "models": "Claude (short style fragment, not a full prompt)",
        "test": lambda t: t.lstrip().lower().startswith("# code comments"),
    },
]


def find_bunfs_paths(data):
    """Map bunfs stem -> full embedded path, e.g. system -> B:/~BUN/root/system-cvb7jtx8.txt."""
    found = {}
    for m in re.finditer(rb"B:/~BUN/root/([a-z0-9-]+)-[a-z0-9]{8}\.txt", data):
        stem = m.group(1).decode()
        found.setdefault(stem, m.group(0).decode())
    return found


def model_family(text):
    for item in MODEL_FAMILIES:
        try:
            if item["test"](text):
                return item
        except Exception:
            continue
    return None


def region_stem(text):
    """bunfs stem for a prompt region, or '' when it is not an embedded file."""
    item = model_family(text)
    if item:
        return item["stem"]
    low = text.lstrip().lower()
    if low.startswith("you are a code reviewer"):
        return "review"
    if low.startswith("you are a file search specialist"):
        return "explore"
    return ""


def classify(text):
    low = text[:6000].lower()
    if "title generator" in low:
        return "title"
    if "context summarization" in low:
        return "summary"
    if "file search specialist" in low:
        return "explore"
    if "code reviewer" in low:
        return "review"
    if "agent architect" in low:
        return "agent-architect"
    if "you are opencode, a coding agent" in low:
        return "main-coding"
    if "you are opencode, an interactive cli" in low:
        return "main-cli"
    if "you are opencode, an interactive general" in low:
        return "main-general"
    if "you are opencode, you and the user" in low:
        return "main-shared"
    if "you are an ai agent running in opencode" in low:
        return "main"
    if "you are an ai agent powered by opencode" in low:
        return "main-harness"
    if "you are opencode" in low:
        return "main-opencode"
    if "you are an ai coding agent" in low:
        return "coding-agent"
    if "# harness" in low:
        return "template"
    if "# code comments" in low:
        return "style"
    return "prompt"


def looks_like_prompt(text):
    """A library error string can start with 'You are ...'; a real prompt is
    long prose with structure, or starts with an OpenCode-specific opener."""
    text = text.lstrip("\r\n\t ")
    if len(text) < MIN_REGION:
        return False
    low = text[:90].lower()

    trusted = (
        low.startswith("you are opencode")
        or low.startswith("you are an ai agent running in opencode")
        or low.startswith("you are an ai agent powered by opencode")
        or low.startswith("# code comments")
    )
    if trusted:
        return True

    structured = (
        low.startswith("you are an ")
        or low.startswith("you are a ")
        or low.startswith("you are the ")
        or low.startswith("you are claude")
        or low.startswith("# harness")
        or low.startswith("# tool usage policy")
    )
    if not structured:
        return False

    newlines = text.count("\n")
    bullets = sum(
        1 for line in text.split("\n")[1:]
        if line.lstrip(" \t").startswith(("-", "#", "*"))
    )
    return newlines >= 3 and bullets >= 2


def _utf8_candidates(data):
    for anchor in UTF8_ANCHORS:
        start = 0
        while True:
            i = data.find(anchor, start)
            if i < 0:
                break
            start = i + len(anchor)
            left = data.rfind(b"\x00", max(0, i - MAX_REGION), i)
            seg_start = left + 1
            seg_end = data.find(b"\x00", i)
            if seg_end < 0:
                seg_end = len(data)
            if seg_end - seg_start > MAX_REGION:
                continue
            yield seg_start, seg_end, "utf8"


def _utf16_candidates(data):
    start = 0
    while True:
        i = data.find(UTF16_ANCHOR, start)
        if i < 0:
            break
        start = i + 2

        # walk back to a NUL code unit, aligned to the anchor: 00 00 can also
        # appear inside text when a character's low byte is zero
        limit = max(0, i - MAX_REGION)
        seg_start = limit
        pos = i
        while True:
            j = data.rfind(b"\x00\x00", limit, pos)
            if j < 0:
                seg_start = limit + ((i - limit) % 2)
                break
            if (i - (j + 2)) % 2 == 0:
                seg_start = j + 2
                break
            pos = j + 1

        cap = min(len(data), i + MAX_REGION)
        seg_end = cap
        pos = i
        while True:
            j = data.find(b"\x00\x00", pos, cap)
            if j < 0:
                seg_end = cap
                break
            if (j - i) % 2 == 0:
                seg_end = j
                break
            pos = j + 1

        if seg_end - seg_start < MIN_REGION or seg_end - seg_start > MAX_REGION:
            continue
        yield seg_start, seg_end, "utf16"


def scan(data):
    found = {}

    for start, end, enc in list(_utf8_candidates(data)) + list(_utf16_candidates(data)):
        length = end - start
        if length < MIN_REGION or length > MAX_REGION or start in found:
            continue

        raw = data[start:end]
        text = raw.decode("utf-16-le" if enc == "utf16" else "utf-8", "replace")

        if not looks_like_prompt(text):
            continue
        if enc == "utf8" and sum(1 for b in raw[:4096] if b < 9) > 40:
            continue

        found[start] = {
            "off": start, "len": length, "enc": enc,
            "text": text, "role": classify(text),
        }

    regions = sorted(found.values(), key=lambda r: r["off"])
    for r in regions:
        r["pristine"] = True
    assign_roles(regions)
    return regions


def assign_roles(regions):
    seen = {}
    for r in sorted(regions, key=lambda x: x["off"]):
        base = r["role"].split("#")[0]
        seen[base] = seen.get(base, 0) + 1
        r["role"] = base if seen[base] == 1 else f"{base}#{seen[base]}"


def decorate(regions):
    for r in regions:
        name = friendly_name(r["role"])
        if r.get("cleared"):
            name += "  (cleared)"
        elif r.get("modified"):
            name += "  (modified)"
        r["name"] = name
        r["type"] = type_of(r["role"])
        r["chars"] = f"{len(r['text']):,}"
    return regions


# ------------------------------------------------------------------ state

def fingerprint(data):
    return f"{len(data)}:{hashlib.sha1(data[:65536]).hexdigest()}"


def load_state(data):
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if state.get("fp") != fingerprint(data):
        return None
    return state.get("regions", [])


def save_state(data, regions):
    try:
        STATE_FILE.write_text(json.dumps({
            "fp": fingerprint(data),
            "regions": [
                {"off": r["off"], "len": r["len"], "enc": r["enc"], "role": r["role"]}
                for r in regions if r.get("pristine")
            ],
        }, indent=1), encoding="utf-8")
    except OSError:
        pass


def merge_state(regions, data):
    """Keep prompts we already knew about visible after they are edited or
    cleared, so they can still be restored one at a time."""
    stored = load_state(data)
    if not stored:
        return regions

    known = {r["off"] for r in regions}
    merged = list(regions)

    for item in stored:
        off, length, enc = item["off"], item["len"], item["enc"]
        if off in known or off + length > len(data):
            continue

        raw = data[off:off + length]
        text = raw.decode("utf-16-le" if enc == "utf16" else "utf-8", "replace")
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
        if printable < len(text) * 0.98:
            continue

        entry = {
            "off": off, "len": length, "enc": enc, "text": text,
            "role": item["role"].split("#")[0],
        }
        if text.strip() == "":
            entry["cleared"] = True
        else:
            entry["modified"] = True
        merged.append(entry)

    merged.sort(key=lambda r: r["off"])
    assign_roles(merged)
    return merged


# ------------------------------------------------------------------ patching

def decode_region(data, r):
    raw = data[r["off"]: r["off"] + r["len"]]
    return raw.decode("utf-16-le") if r["enc"] == "utf16" else raw.decode("utf-8", "replace")


def encode_text(text, enc):
    return text.encode("utf-16-le") if enc == "utf16" else text.encode("utf-8")


def patch_bytes(path, region, payload):
    room = region["len"]
    if len(payload) > room:
        return False, "too long"

    if region["enc"] == "utf16":
        filler = (b" \x00") * (room // 2)
        if len(filler) < room:
            filler += b"\x00"
    else:
        filler = b" " * room

    blob = payload + filler[len(payload): room]

    with open(path, "r+b") as f:
        f.seek(region["off"])
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    return True, ""


# ------------------------------------------------------------------ backups

def newest(prefix, suffix):
    if not BACKUP_DIR.exists():
        return None
    items = sorted(
        (p for p in BACKUP_DIR.iterdir()
         if p.name.startswith(prefix) and p.name.endswith(suffix)),
        key=lambda p: p.name,
    )
    return items[-1] if items else None


def write_dump(regions, data, folder):
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in regions:
        name = f"{r['role'].replace('#', '-')}-0x{r['off']:08X}.{r['enc']}.txt"
        (folder / name).write_text(decode_region(data, r), encoding="utf-8", newline="")
        lines.append(f"0x{r['off']:08X}|{name}")
    (folder / "index.txt").write_text("\n".join(lines), encoding="utf-8")


def pretty_time(stamp):
    try:
        return time.strftime("%b %d, %Y - %H:%M", time.strptime(stamp, "%Y%m%d-%H%M%S"))
    except ValueError:
        return stamp


# ------------------------------------------------------------------ gui

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenCode Prompt Manager")
        self.configure(bg="white")
        self._center(1060, 720)
        self.minsize(940, 660)

        self.exe_path = tk.StringVar(value=str(default_binary()))
        self.data = b""
        self.regions = []
        self.by_iid = {}
        self.mby_iid = {}

        self._style()
        self._build()
        self.reload()

    def _center(self, w, h):
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _style(self):
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("vista")
        except tk.TclError:
            pass
        self.style.configure("Treeview", font=("Segoe UI", 10), rowheight=28,
                             fieldbackground="white")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.style.configure("TButton", font=("Segoe UI", 10), padding=(8, 8))

    def _build(self):
        shell = tk.Frame(self, bg="white")
        shell.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(shell, bg="white", highlightthickness=0)
        vbar = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        inner = tk.Frame(self.canvas, bg="white")
        self.inner = inner
        window = self.canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        tk.Label(inner, text="OpenCode Prompt Manager", bg="white", fg="#111",
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=24, pady=(20, 0))
        tk.Label(inner, text="Back up, replace, or clear the instructions OpenCode sends.",
                 bg="white", fg="#666", font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(2, 16))

        file_row = tk.Frame(inner, bg="white")
        file_row.pack(fill="x", padx=24)
        tk.Label(file_row, text="OpenCode file", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(anchor="w")
        row = tk.Frame(file_row, bg="white")
        row.pack(fill="x", pady=(4, 0))
        ttk.Entry(row, textvariable=self.exe_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Reload", command=self.reload).pack(side="left", padx=(8, 0))

        self.notebook = ttk.Notebook(inner)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(16, 0))

        self.prompts_tab = tk.Frame(self.notebook, bg="white")
        self.models_tab = tk.Frame(self.notebook, bg="white")
        self.notebook.add(self.prompts_tab, text="  Prompts  ")
        self.notebook.add(self.models_tab, text="  Model prompts  ")

        self._build_prompts_tab()
        self._build_models_tab()

        buttons = tk.Frame(inner, bg="white")
        buttons.pack(fill="x", padx=24, pady=(16, 0))
        for text, cmd in (
            ("Dump prompts", self.dump_now),
            ("Replace selected", self.replace),
            ("Clear selected", self.clear),
            ("Restore selected", self.restore_one),
            ("Restore all", self.restore_all),
        ):
            ttk.Button(buttons, text=text, command=cmd).pack(
                side="left", expand=True, fill="x", padx=(0, 8))

        self.status = tk.Label(inner, text="", bg="white", fg="#555", font=("Segoe UI", 9))
        self.status.pack(anchor="w", padx=24, pady=(14, 2))
        tk.Label(inner, text="Every change saves a backup first. Restore puts the previous version back.",
                 bg="white", fg="#999", font=("Segoe UI", 8)).pack(anchor="w", padx=24, pady=(0, 14))

    def _on_wheel(self, event):
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            under = None
        if isinstance(under, tk.Text):
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_prompts_tab(self):
        body = tk.Frame(self.prompts_tab, bg="white")
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(body, bg="white")
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(left, columns=("type", "path", "chars"),
                                 show="tree headings", height=9, selectmode="browse")
        self.tree.heading("#0", text="Prompt", anchor="w")
        self.tree.heading("type", text="Type", anchor="w")
        self.tree.heading("path", text="Embedded path", anchor="w")
        self.tree.heading("chars", text="Size", anchor="e")
        self.tree.column("#0", width=250, anchor="w")
        self.tree.column("type", width=120, anchor="w")
        self.tree.column("path", width=330, anchor="w")
        self.tree.column("chars", width=80, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        right = tk.Frame(body, bg="white")
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))
        tk.Label(right, text="Preview", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(anchor="w")
        box = tk.Frame(right, bg="white")
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.preview = tk.Text(box, wrap="word", font=("Segoe UI", 10), bd=1, relief="solid",
                               bg="#fbfbfb", padx=8, pady=8, height=8, width=30, state="disabled")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=bar.set)
        self.preview.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

    def _build_models_tab(self):
        tk.Label(self.models_tab,
                 text="Which prompt each model family receives, and where it lives in the file.",
                 bg="white", fg="#666", font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 8))

        cols = ("models", "path", "size")
        self.mtree = ttk.Treeview(self.models_tab, columns=cols, show="tree headings",
                                  height=7, selectmode="browse")
        self.mtree.heading("#0", text="Family", anchor="w")
        self.mtree.heading("models", text="Models", anchor="w")
        self.mtree.heading("path", text="Embedded path", anchor="w")
        self.mtree.heading("size", text="Size", anchor="e")
        self.mtree.column("#0", width=150, anchor="w")
        self.mtree.column("models", width=320, anchor="w")
        self.mtree.column("path", width=390, anchor="w")
        self.mtree.column("size", width=80, anchor="e")
        self.mtree.pack(fill="both", expand=True)
        self.mtree.bind("<<TreeviewSelect>>", self.on_model_select)

        tk.Label(self.models_tab, text="Preview", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 4))
        box = tk.Frame(self.models_tab, bg="white")
        box.pack(fill="both", expand=True)
        self.mpreview = tk.Text(box, wrap="word", font=("Segoe UI", 10), bd=1, relief="solid",
                                bg="#fbfbfb", padx=8, pady=8, height=9, width=30, state="disabled")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.mpreview.yview)
        self.mpreview.configure(yscrollcommand=bar.set)
        self.mpreview.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.mtree.bind("<Double-1>", lambda _e: self.copy_model_field("path"))


    # ---------------- data

    def reload(self):
        path = Path(self.exe_path.get())
        if not path.exists():
            self.status.config(text="Choose the OpenCode file first.")
            return
        try:
            self.data = path.read_bytes()
        except OSError as exc:
            messagebox.showerror("Cannot read", str(exc))
            return

        self.regions = decorate(merge_state(scan(self.data), self.data))
        save_state(self.data, self.regions)

        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        paths = find_bunfs_paths(self.data)
        for r in self.regions:
            iid = f"0x{r['off']:08X}"
            stem = region_stem(r["text"])
            r["path"] = paths.get(stem, "") if stem else ""
            self.tree.insert("", "end", iid=iid, text=r["name"],
                             values=(r["type"],
                                     r["path"] or "-",
                                     f"{len(r['text']):,}"))
            self.by_iid[iid] = r

        self.refresh_models()

        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.config(state="disabled")

        if self.regions:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.status.config(text=f"{len(self.regions)} prompts found in this file.")
        else:
            self.status.config(text="No prompts in this file. It is already clean.")

    def selected(self):
        try:
            on_models = self.notebook.index(self.notebook.select()) == 1
        except tk.TclError:
            on_models = False

        if on_models:
            sel = self.mtree.selection()
            if not sel:
                messagebox.showinfo("Nothing selected", "Pick a model family first.")
                return None
            _item, match = self.mby_iid.get(sel[0], (None, None))
            if not match:
                messagebox.showinfo(
                    "No prompt here",
                    "This model family has no prompt in the selected file.")
                return None
            return match

        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Pick a prompt from the list first.")
            return None
        return self.by_iid[sel[0]]

    def on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        text = self.by_iid[sel[0]]["text"]
        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.config(state="disabled")
        self.preview.yview_moveto(0)

    # ---------------- model families

    def refresh_models(self):
        if not hasattr(self, "mtree"):
            return
        self.mtree.delete(*self.mtree.get_children())
        self.mby_iid = {}
        paths = find_bunfs_paths(self.data)
        used = set()

        for idx, item in enumerate(MODEL_FAMILIES):
            match = None
            for r in self.regions:
                if r["off"] in used:
                    continue
                try:
                    if item["test"](r["text"]):
                        match = r
                        break
                except Exception:
                    continue
            if match:
                used.add(match["off"])

            label = item["name"] if match else item["name"] + "  (not in this build)"
            iid = f"model-{idx}"
            self.mtree.insert(
                "", "end", iid=iid, text=label,
                values=(
                    item["models"],
                    paths.get(item["stem"], "(inline, no embedded file)"),
                    f"{match['len']:,} B" if match else "-",
                ))
            self.mby_iid[iid] = (item, match)

        self.mpreview.config(state="normal")
        self.mpreview.delete("1.0", "end")
        self.mpreview.config(state="disabled")

        children = self.mtree.get_children()
        if children:
            self.mtree.selection_set(children[0])
            self.mtree.focus(children[0])

    def on_model_select(self, _event=None):
        sel = self.mtree.selection()
        if not sel or not hasattr(self, "mpreview"):
            return
        _item, match = self.mby_iid.get(sel[0], (None, None))
        self.mpreview.config(state="normal")
        self.mpreview.delete("1.0", "end")
        if match:
            self.mpreview.insert("1.0", match["text"])
        else:
            self.mpreview.insert("1.0", "This model family has no prompt in the selected file.")
        self.mpreview.config(state="disabled")
        self.mpreview.yview_moveto(0)

    def copy_model_field(self, field):
        sel = self.mtree.selection()
        if not sel:
            return
        item, match = self.mby_iid.get(sel[0], (None, None))
        if item is None:
            return
        if field == "path":
            value = find_bunfs_paths(self.data).get(item["stem"], "")
        else:
            value = f"0x{match['off']:08X}" if match else ""
        if not value:
            messagebox.showinfo("Nothing to copy", "This prompt has no embedded path.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.config(text=f"Copied: {value}")

    def show_model_in_prompts(self):
        sel = self.mtree.selection()
        if not sel:
            return
        _item, match = self.mby_iid.get(sel[0], (None, None))
        if not match:
            messagebox.showinfo("Not found", "This model family has no prompt in the file.")
            return
        iid = f"0x{match['off']:08X}"
        if iid in self.by_iid:
            self.notebook.select(self.prompts_tab)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)


    # ---------------- actions

    def browse(self):
        path = filedialog.askopenfilename(
            title="Where is opencode.exe?",
            filetypes=[("OpenCode", "opencode.exe"), ("Programs", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.exe_path.set(path)
            self.reload()

    def backup(self):
        exe = Path(self.exe_path.get())
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            write_dump(self.regions, self.data, BACKUP_DIR / f"dump-{ts}")
            shutil.copy2(exe, BACKUP_DIR / f"opencode-{ts}.exe")
        except OSError as exc:
            messagebox.showerror("Backup failed", f"Nothing was changed.\n\n{exc}")
            self.status.config(text="Backup failed. Nothing changed.")
            return False
        self.status.config(text="Backup saved.")
        return True

    def apply(self, region, payload, done_text):
        ok, err = patch_bytes(Path(self.exe_path.get()), region, payload)
        if not ok:
            if err == "too long":
                messagebox.showwarning(
                    "Text is too long",
                    "That text is longer than the space available for this prompt.\n\n"
                    "Shorten it a little and try again.")
            else:
                messagebox.showerror("Failed", err)
            return False
        self.reload()
        self.status.config(text=done_text)
        return True

    def dump_now(self):
        if not self.regions:
            messagebox.showinfo("Dump", "No prompts to save in this file.")
            return
        ts = time.strftime("%Y%m%d-%H%M%S")
        folder = BACKUP_DIR / f"manual-{ts}"
        try:
            write_dump(self.regions, self.data, folder)
        except OSError as exc:
            messagebox.showerror("Dump failed", str(exc))
            return
        self.status.config(text=f"Saved {len(self.regions)} prompts to backups\\manual-{ts}")
        messagebox.showinfo("Dump saved", f"{len(self.regions)} prompts written to:\n\n{folder}")

    def replace(self):
        r = self.selected()
        if not r:
            return
        path = filedialog.askopenfilename(
            title="Choose the replacement prompt",
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        payload = encode_text(text, r["enc"])
        if len(payload) > r["len"]:
            messagebox.showwarning(
                "Text is too long",
                f"This prompt has room for {r['len']} bytes, your text needs {len(payload)}.\n\n"
                "Shorten it and try again.")
            return
        if not messagebox.askyesno(
                "Replace prompt",
                f'Replace "{r["name"]}" with the contents of:\n\n{path}\n\nA backup is saved first.'):
            return
        if self.backup():
            self.apply(r, payload, f'Replaced "{r["name"]}".')

    def clear(self):
        r = self.selected()
        if not r:
            return
        if not messagebox.askyesno(
                "Clear prompt",
                f'Remove "{r["name"]}"?\n\n'
                "A backup is saved first. Use Restore selected to bring it back."):
            return
        if self.backup():
            self.apply(r, encode_text(" ", r["enc"]), f'Removed "{r["name"]}".')

    def restore_one(self):
        r = self.selected()
        if not r:
            return
        dump = newest("dump-", "")
        if dump is None:
            messagebox.showinfo("Restore", "No backups yet.")
            return

        index = dump / "index.txt"
        if not index.exists():
            messagebox.showinfo("Restore", "The latest backup has no index.")
            return

        match = None
        want = f"0X{r['off']:08X}"
        for line in index.read_text(encoding="utf-8").splitlines():
            off, _, name = line.partition("|")
            if off.strip().upper() == want:
                match = name.strip()
                break

        if not match:
            messagebox.showinfo(
                "Restore",
                "This prompt is not in the latest backup.\n\n"
                "Use Restore all to go back to the previous version.")
            return

        src = dump / match
        if not src.exists():
            messagebox.showerror("Restore", "The saved copy of this prompt is missing.")
            return

        text = src.read_text(encoding="utf-8")
        if not messagebox.askyesno("Restore prompt", f'Put "{r["name"]}" back to its saved text?'):
            return
        if self.backup():
            self.apply(r, encode_text(text, r["enc"]), f'Restored "{r["name"]}".')

    def restore_all(self):
        bak = newest("opencode-", ".exe")
        if bak is None:
            messagebox.showinfo("Restore", "No backups yet.")
            return

        stamp = bak.stem.split("-", 1)[-1]
        pretty = pretty_time(stamp)
        if not messagebox.askyesno("Restore all",
                                   f"Put the whole file back to the version saved {pretty}?"):
            return

        exe = Path(self.exe_path.get())
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            write_dump(self.regions, self.data, BACKUP_DIR / f"dump-{ts}")
            shutil.copy2(exe, BACKUP_DIR / f"opencode-{ts}.exe")
            shutil.copy2(bak, exe)
        except OSError as exc:
            messagebox.showerror("Restore failed", f"Is OpenCode still running?\n\n{exc}")
            self.status.config(text="Restore failed.")
            return

        self.reload()
        self.status.config(text=f"Restored the version from {pretty}.")


if __name__ == "__main__":
    App().mainloop()
