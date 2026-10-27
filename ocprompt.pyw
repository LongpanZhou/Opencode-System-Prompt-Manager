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

Restore always goes back to the true original: backups/dump-original and
opencode-original.exe, written the first time a still-unmodified file is
changed. A replacement may be shorter than the original; the leftover space
is padded with blanks and never shown as part of the message.
"""

import filecmp
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

# The untouched copy of the loaded exe and its text dump. Restore always
# goes back to these, never to the newest change backup.
ORIGINAL_EXE = BACKUP_DIR / "opencode-original.exe"
ORIGINAL_DUMP = BACKUP_DIR / "dump-original"
ORIGINAL_META = BACKUP_DIR / "original.json"

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
        text = clean_text(raw.decode("utf-16-le" if enc == "utf16" else "utf-8", "replace"))

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
    return regions


# ------------------------------------------------------------------ state

def fingerprint(data):
    return f"{len(data)}:{hashlib.sha1(data[:65536]).hexdigest()}"


def load_state(data):
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("fp") != fingerprint(data):
        return None
    regions = state.get("regions", [])
    return regions if isinstance(regions, list) else None


def save_state(data, regions):
    """Remember every known region — modified and cleared ones too, so they
    stay in the list after a reload."""
    try:
        STATE_FILE.write_text(json.dumps({
            "fp": fingerprint(data),
            "regions": [
                {"off": r["off"], "len": r["len"], "enc": r["enc"], "role": r["role"]}
                for r in regions
            ],
        }, indent=1), encoding="utf-8")
    except OSError:
        pass


def original_region_records(data):
    """off -> {"len", "enc", "role"} for every prompt in the original dump,
    when that dump belongs to the loaded build. Used to bring back prompts
    the state file no longer remembers."""
    meta = load_original_meta()
    if meta.get("fp") != fingerprint(data) or not ORIGINAL_DUMP.is_dir():
        return {}
    try:
        lines = (ORIGINAL_DUMP / "index.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    records = {}
    for line in lines:
        off_s, _, name = line.partition("|")
        name = name.strip()
        try:
            off = int(off_s.strip(), 16)
        except ValueError:
            continue
        parts = name.rsplit(".", 2)
        if len(parts) != 3 or parts[-1] != "txt" or parts[1] not in ("utf8", "utf16"):
            continue
        enc = parts[1]
        try:
            raw = (ORIGINAL_DUMP / name).read_bytes()
        except OSError:
            continue
        text_raw = raw.decode("utf-8", "replace")
        if enc == "utf16":
            length = 2 * len(text_raw)
        else:
            length = len(raw)
        role = re.sub(r"-(\d+)$", r"#\1", parts[0].partition("-0x")[0])
        records[off] = {"len": length, "enc": enc, "role": role,
                        "text": clean_text(text_raw)}
    return records


def merge_state(regions, data):
    """Keep prompts we already knew about visible after they are edited or
    cleared, so they can still be restored one at a time."""
    stored = load_state(data)
    known = {r["off"] for r in regions}
    stored_offs = {item["off"] for item in stored or []}
    merged = list(regions)

    def resurrect(off, length, enc, role):
        if off in known or off + length > len(data):
            return
        raw = data[off:off + length]
        text = clean_text(raw.decode("utf-16-le" if enc == "utf16" else "utf-8", "replace"))
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
        if printable < len(text) * 0.98:
            return
        entry = {
            "off": off, "len": length, "enc": enc, "text": text,
            "role": role.split("#")[0],
        }
        if text.strip() == "":
            entry["cleared"] = True
        else:
            entry["modified"] = True
        merged.append(entry)

    for item in stored or []:
        if not isinstance(item, dict):
            continue
        off, length, enc = item.get("off"), item.get("len"), item.get("enc")
        role = item.get("role")
        if (not isinstance(off, int) or not isinstance(length, int)
                or enc not in ("utf8", "utf16") or not isinstance(role, str)):
            continue
        resurrect(off, length, enc, role)

    # Prompts the state file forgot about (cleared or replaced before it kept
    # them) come back through the original dump.
    for off, rec in original_region_records(data).items():
        if off in stored_offs:
            continue
        resurrect(off, rec["len"], rec["enc"], rec["role"])

    merged.sort(key=lambda r: r["off"])
    assign_roles(merged)
    return merged


def load_original_meta():
    try:
        return json.loads(ORIGINAL_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_original_meta(fp):
    try:
        old = load_original_meta()
        served = set(old.get("served") or [])
        served.add(fp)
        ORIGINAL_META.write_text(json.dumps({
            "fp": fp,
            "saved": time.strftime("%Y-%m-%d %H:%M"),
            "served": sorted(served),
        }, indent=1), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ patching

def decode_region(data, r):
    raw = data[r["off"]: r["off"] + r["len"]]
    text = raw.decode("utf-16-le", "replace") if r["enc"] == "utf16" else raw.decode("utf-8", "replace")
    return clean_text(text)


def clean_text(text):
    """A shorter replacement is padded with spaces up to the original length;
    that filler is not part of the message."""
    return text.rstrip(" \t")


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

def write_dump(regions, data, folder):
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for r in regions:
        name = f"{r['role'].replace('#', '-')}-0x{r['off']:08X}.{r['enc']}.txt"
        (folder / name).write_text(decode_region(data, r), encoding="utf-8", newline="")
        lines.append(f"0x{r['off']:08X}|{name}")
    (folder / "index.txt").write_text("\n".join(lines), encoding="utf-8")


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
        self._preview_iid = None
        self._mpreview_iid = None
        self._mid_apply = False
        self._skip_model_guard = False

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
        # Bottom rows are packed first (side="bottom") so they are always
        # fully visible; when space runs short the tab area shrinks instead.
        tk.Label(self, text="Type in the preview boxes and press Save (or Ctrl+S) to write your text back. "
                            "Every change saves a backup first; Restore puts the original version back.",
                 bg="white", fg="#999", font=("Segoe UI", 8)).pack(
                     side="bottom", anchor="w", padx=24, pady=(0, 14))
        self.status = tk.Label(self, text="", bg="white", fg="#555", font=("Segoe UI", 9))
        self.status.pack(side="bottom", anchor="w", padx=24, pady=(14, 2))

        buttons = tk.Frame(self, bg="white")
        buttons.pack(side="bottom", fill="x", padx=24, pady=(16, 0))
        for text, cmd in (
            ("Dump prompts", self.dump_now),
            ("Replace selected", self.replace),
            ("Clear selected", self.clear),
            ("Restore selected", self.restore_one),
            ("Restore all", self.restore_all),
        ):
            ttk.Button(buttons, text=text, command=cmd).pack(
                side="left", expand=True, fill="x", padx=(0, 8))

        tk.Label(self, text="OpenCode Prompt Manager", bg="white", fg="#111",
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=24, pady=(20, 0))
        tk.Label(self, text="Back up, replace, or clear the instructions OpenCode sends.",
                 bg="white", fg="#666", font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(2, 16))

        file_row = tk.Frame(self, bg="white")
        file_row.pack(fill="x", padx=24)
        tk.Label(file_row, text="OpenCode file", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(anchor="w")
        row = tk.Frame(file_row, bg="white")
        row.pack(fill="x", pady=(4, 0))
        entry = ttk.Entry(row, textvariable=self.exe_path)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.reload())
        ttk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Reload", command=self.reload).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Apply to installed",
                   command=self.apply_to_installed).pack(side="left", padx=(8, 0))

        # packed last: takes all remaining space and shrinks first
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(16, 0))

        self.prompts_tab = tk.Frame(self.notebook, bg="white")
        self.models_tab = tk.Frame(self.notebook, bg="white")
        self.notebook.add(self.prompts_tab, text="  Prompts  ")
        self.notebook.add(self.models_tab, text="  Model prompts  ")

        self._build_prompts_tab()
        self._build_models_tab()

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
        head = tk.Frame(right, bg="white")
        head.pack(fill="x")
        tk.Label(head, text="Preview", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(side="left")
        ttk.Button(head, text="Copy", width=7,
                   command=lambda: self._copy_box(self.preview)).pack(side="right")
        ttk.Button(head, text="Save", width=7,
                   command=self.save_preview).pack(side="right", padx=(0, 6))
        box = tk.Frame(right, bg="white")
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.preview = self._make_preview(box, self.save_preview, height=8)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=bar.set)
        self.preview.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

    def _build_models_tab(self):
        tk.Label(self.models_tab,
                 text="Which prompt each model family receives, and where it lives in the file.",
                 bg="white", fg="#666", font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 8))

        cols = ("prompt", "models", "path", "size")
        self.mtree = ttk.Treeview(self.models_tab, columns=cols, show="tree headings",
                                  height=7, selectmode="browse")
        self.mtree.heading("#0", text="Family", anchor="w")
        self.mtree.heading("prompt", text="Stored prompt", anchor="w")
        self.mtree.heading("models", text="Models", anchor="w")
        self.mtree.heading("path", text="Embedded path", anchor="w")
        self.mtree.heading("size", text="Size", anchor="e")
        self.mtree.column("#0", width=150, anchor="w")
        self.mtree.column("prompt", width=190, anchor="w")
        self.mtree.column("models", width=250, anchor="w")
        self.mtree.column("path", width=300, anchor="w")
        self.mtree.column("size", width=70, anchor="e")
        self.mtree.pack(fill="both", expand=True)
        self.mtree.bind("<<TreeviewSelect>>", self.on_model_select)

        head = tk.Frame(self.models_tab, bg="white")
        head.pack(fill="x", pady=(12, 4))
        tk.Label(head, text="Preview", bg="white", fg="#444",
                 font=("Segoe UI", 9)).pack(side="left")
        ttk.Button(head, text="Copy", width=7,
                   command=lambda: self._copy_box(self.mpreview)).pack(side="right")
        ttk.Button(head, text="Save", width=7,
                   command=self.save_mpreview).pack(side="right", padx=(0, 6))
        box = tk.Frame(self.models_tab, bg="white")
        box.pack(fill="both", expand=True)
        self.mpreview = self._make_preview(box, self.save_mpreview, height=9)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.mpreview.yview)
        self.mpreview.configure(yscrollcommand=bar.set)
        self.mpreview.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        self.mtree.bind("<Double-1>", lambda _e: self.copy_model_field("path"))

        mmenu = tk.Menu(self.mtree, tearoff=0)
        mmenu.add_command(label="Show in Prompts tab", command=self.show_model_in_prompts)
        mmenu.add_command(label="Copy embedded path",
                          command=lambda: self.copy_model_field("path"))

        def mmenu_popup(event):
            iid = self.mtree.identify_row(event.y)
            if not iid:
                return "break"
            sel = self.mtree.selection()
            if sel and sel[0] != iid:
                # settle unsaved edits before the menu grabs the pointer
                _item, match = self.mby_iid.get(sel[0], (None, None))
                ref = match["text"] if match else \
                    "This model family has no prompt in the selected file."
                if self._box_dirty(self.mpreview, ref):
                    name = (match or _item or {}).get("name", "this family")
                    if not messagebox.askyesno(
                            "Unsaved changes",
                            f'"{name}" has unsaved edits in the box.\n\nDiscard them?'):
                        return "break"
                    self._skip_model_guard = True   # decision already made
            self.mtree.selection_set(iid)
            self.mtree.focus(iid)
            try:
                mmenu.tk_popup(event.x_root, event.y_root)
            finally:
                mmenu.grab_release()
            return "break"

        self.mtree.bind("<Button-3>", mmenu_popup)

    def _make_preview(self, parent, save_cmd, height):
        """A preview box the user can edit; Save writes it back to the file."""
        t = tk.Text(parent, wrap="word", font=("Segoe UI", 10), bd=1, relief="solid",
                    bg="#fbfbfb", padx=8, pady=8, height=height, width=30, undo=True)
        menu = tk.Menu(t, tearoff=0)
        for label, seq in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"), ("Paste", "<<Paste>>")):
            menu.add_command(label=label, command=lambda s=seq: t.event_generate(s))
        menu.add_separator()
        menu.add_command(label="Select all",
                         command=lambda: t.tag_add("sel", "1.0", "end-1c"))
        menu.add_command(label="Save", command=save_cmd)

        def rmenu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        t.bind("<Control-s>", lambda _e: (save_cmd(), "break")[1])
        t.bind("<Control-a>", lambda _e: (t.tag_add("sel", "1.0", "end-1c"), "break")[1])
        t.bind("<Button-3>", rmenu)
        return t

    @staticmethod
    def _fill_box(box, text):
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.yview_moveto(0)

    @staticmethod
    def _box_dirty(box, ref_text):
        """True when the box holds something other than its prompt's text.
        Content-based, because the Text <<Modified>> flag is unreliable when
        several of its events are pending at once."""
        return clean_text(box.get("1.0", "end-1c")) != clean_text(ref_text)

    def _unsaved_text(self):
        """True when either preview box holds unsaved edits."""
        sel = self.tree.selection()
        if sel and sel[0] in self.by_iid:
            if self._box_dirty(self.preview, self.by_iid[sel[0]]["text"]):
                return True
        elif self.preview.get("1.0", "end-1c").strip():
            return True
        msel = self.mtree.selection()
        if msel:
            _item, match = self.mby_iid.get(msel[0], (None, None))
            ref = match["text"] if match else \
                "This model family has no prompt in the selected file."
            if self._box_dirty(self.mpreview, ref):
                return True
        return False

    def _copy_box(self, box):
        sel = box.tag_ranges("sel")
        text = box.get("sel.first", "sel.last") if sel else box.get("1.0", "end-1c")
        if not text:
            self.status.config(text="Nothing to copy.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.config(text=f"Copied {len(text):,} characters.")

    def save_preview(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Pick a prompt from the list first.")
            return
        self._save_box(self.preview, self.by_iid[sel[0]])

    def save_mpreview(self):
        sel = self.mtree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Pick a model family first.")
            return
        _item, match = self.mby_iid.get(sel[0], (None, None))
        if not match:
            messagebox.showinfo("No prompt here",
                                "This model family has no prompt in the selected file.")
            return
        self._save_box(self.mpreview, match)

    def _save_box(self, box, r):
        text = box.get("1.0", "end-1c")
        if clean_text(text) == clean_text(r["text"]):
            self.status.config(text=f'"{r["name"]}" is unchanged.')
            return
        payload = encode_text(text, r["enc"])
        if len(payload) > r["len"]:
            self._too_long_warning(r, text, payload)
            return
        if not messagebox.askyesno(
                "Save prompt",
                f'Save the box contents to "{r["name"]}"?\n\n'
                "A backup is saved first."):
            return
        if self.backup():
            self.apply(r, payload, f'Saved "{r["name"]}".')

    def _too_long_warning(self, r, text, payload):
        if r["enc"] == "utf16":
            room, need, unit = r["len"] // 2, len(payload) // 2, "characters"
        else:
            room, need, unit = r["len"], len(payload), "bytes"
        messagebox.showwarning(
            "Text is too long",
            f'The space for "{r["name"]}" holds about {room:,} {unit}.\n'
            f"Your text needs {need:,} {unit}.\n\n"
            "A replacement can only be as long as the prompt it replaces — "
            "shorten the text and try again. Anything shorter is fine: the "
            "leftover space is padded automatically.\n\n"
            'Tip: a prompt set in opencode.jsonc (agent "system" setting) has '
            "no length limit, but that is outside this tool.")

    # ---------------- data

    def reload(self):
        if self._unsaved_text() and not self._mid_apply:
            if not messagebox.askyesno(
                    "Unsaved changes",
                    "Discard unsaved edits in the preview boxes and reload?"):
                return
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

        self.preview.delete("1.0", "end")

        if self.regions:
            first = self.tree.get_children()[0]
            self._preview_iid = None
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
        iid = sel[0]
        prev = self._preview_iid
        if iid == prev:
            return
        if prev in self.by_iid:
            old = self.by_iid[prev]
            if self._box_dirty(self.preview, old["text"]):
                if not messagebox.askyesno(
                        "Unsaved changes",
                        f'"{old["name"]}" has unsaved edits in the box.\n\n'
                        "Discard them?"):
                    self.tree.selection_set(prev)
                    self.tree.focus(prev)
                    return
        self._preview_iid = iid
        self._fill_box(self.preview, self.by_iid[iid]["text"])

    # ---------------- model families

    def refresh_models(self):
        if not hasattr(self, "mtree"):
            return
        self.mtree.delete(*self.mtree.get_children())
        self.mby_iid = {}
        paths = find_bunfs_paths(self.data)
        used = set()

        # what each family received in the untouched build, so cleared or
        # replaced prompts can be labelled instead of "not in this build"
        original = original_region_records(self.data)
        orig_stem = {}
        for item in MODEL_FAMILIES:
            for off, rec in sorted(original.items()):
                try:
                    if item["test"](rec["text"]):
                        orig_stem[item["stem"]] = off
                        break
                except Exception:
                    continue

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

            label = item["name"]
            stored = "-"
            if match:
                used.add(match["off"])
                stored = match["name"]
            else:
                off = orig_stem.get(item["stem"])
                prev = next((r for r in self.regions if r["off"] == off), None) \
                    if off is not None else None
                if prev is not None:
                    stored = prev["name"]
                    if prev.get("cleared") or clean_text(prev["text"]) == "":
                        label += "  (cleared)"
                    elif prev.get("modified") or clean_text(prev["text"]) != original[off]["text"]:
                        label += "  (replaced)"
                    else:
                        stored = "-"
                        label += "  (not in this build)"
                else:
                    label += "  (not in this build)"

            iid = f"model-{idx}"
            self.mtree.insert(
                "", "end", iid=iid, text=label,
                values=(
                    stored,
                    item["models"],
                    paths.get(item["stem"], "(inline, no embedded file)"),
                    f"{match['len']:,} B" if match else "-",
                ))
            self.mby_iid[iid] = (item, match)

        self.mpreview.delete("1.0", "end")
        self._mpreview_iid = None

        children = self.mtree.get_children()
        if children:
            self.mtree.selection_set(children[0])
            self.mtree.focus(children[0])

    def on_model_select(self, _event=None):
        sel = self.mtree.selection()
        if not sel or not hasattr(self, "mpreview"):
            return
        _item, match = self.mby_iid.get(sel[0], (None, None))
        prev = self._mpreview_iid
        if sel[0] == prev:
            return
        if getattr(self, "_skip_model_guard", False):
            self._skip_model_guard = False
        elif prev is not None:
            prev_item, prev_match = self.mby_iid.get(prev, (None, None))
            ref = prev_match["text"] if prev_match else \
                "This model family has no prompt in the selected file."
            if self._box_dirty(self.mpreview, ref):
                name = (prev_match or prev_item or {}).get("name", "This family")
                if not messagebox.askyesno(
                        "Unsaved changes",
                        f'"{name}" has unsaved edits in the box.\n\nDiscard them?'):
                    self.mtree.selection_set(prev)
                    self.mtree.focus(prev)
                    return
        self._mpreview_iid = sel[0]
        if match:
            self._fill_box(self.mpreview, match["text"])
        else:
            self._fill_box(self.mpreview, "This model family has no prompt in the selected file.")

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

    def apply_to_installed(self):
        """Push the copy being edited over the installed opencode.exe, so
        real `opencode` runs use these prompts too."""
        exe = Path(self.exe_path.get())
        if not exe.exists():
            messagebox.showinfo("Apply to installed", "Load a file first.")
            return
        if not INSTALLED_BIN.parent.exists():
            messagebox.showinfo(
                "Apply to installed",
                "The installed OpenCode location does not exist:\n\n"
                f"{INSTALLED_BIN.parent}")
            return
        if self._same_file(exe, INSTALLED_BIN):
            messagebox.showinfo("Apply to installed", "The installed exe is already identical.")
            return

        warn = ""
        if INSTALLED_BIN.exists():
            mine, theirs = exe.stat().st_size, INSTALLED_BIN.stat().st_size
            if mine != theirs:
                warn = (f"\nNote: the installed exe is a different build "
                       f"({theirs:,} vs {mine:,} bytes) — applying replaces "
                       "it entirely.\n")
        if not messagebox.askyesno(
                "Apply to installed",
                f"Copy this exe over the installed OpenCode?\n\n{INSTALLED_BIN}\n"
                f"{warn}\nA backup of the installed exe is saved first. "
                "Close OpenCode now. An npm update replaces it again."):
            return
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            if INSTALLED_BIN.exists():
                shutil.copy2(INSTALLED_BIN,
                             BACKUP_DIR / f"installed-{time.strftime('%Y%m%d-%H%M%S')}.exe")
            shutil.copy2(exe, INSTALLED_BIN)
        except OSError as exc:
            messagebox.showerror("Apply failed", f"Is OpenCode still running?\n\n{exc}")
            self.status.config(text="Apply failed.")
            return
        self.status.config(text=f"Installed copy replaced:\n{INSTALLED_BIN}")

    def backup(self):
        exe = Path(self.exe_path.get())
        if not self._exe_current():
            messagebox.showerror(
                "File changed",
                "The file at this path is not the one that was loaded.\n\n"
                "Press Reload first. Nothing was changed.")
            self.status.config(text="Nothing changed — press Reload.")
            return False
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            write_dump(self.regions, self.data, BACKUP_DIR / f"dump-{ts}")
            shutil.copy2(exe, BACKUP_DIR / f"opencode-{ts}.exe")
        except OSError as exc:
            messagebox.showerror("Backup failed", f"Nothing was changed.\n\n{exc}")
            self.status.config(text="Backup failed. Nothing changed.")
            return False

        # While the file is still untouched, this snapshot IS the true original.
        # Keep it under a fixed name so Restore always goes back to it.
        note = ""
        if self.is_pristine():
            try:
                if not (ORIGINAL_EXE.exists() and filecmp.cmp(exe, ORIGINAL_EXE, shallow=False)):
                    write_dump(self.regions, self.data, ORIGINAL_DUMP)
                    shutil.copy2(exe, ORIGINAL_EXE)
                    save_original_meta(fingerprint(self.data))
                    note = " Original saved."
            except OSError as exc:
                note = f" Could not save the original: {exc}"
        self.status.config(text="Backup saved." + note)
        return True

    def is_pristine(self):
        """True when the loaded file is still completely unmodified."""
        if not self.regions:
            return False
        meta = load_original_meta()
        fp = fingerprint(self.data)
        if meta.get("fp") == fp and ORIGINAL_EXE.exists():
            return self._same_file(Path(self.exe_path.get()), ORIGINAL_EXE)
        if fp in (meta.get("served") or []):
            # this build already had its original recorded once: the current
            # file cannot be trusted as a fresh original, even if every
            # region still scans as a prompt
            return False
        return all(r.get("pristine") for r in self.regions)

    def _exe_current(self):
        """True when the file on disk is still the one the regions came from."""
        try:
            with open(self.exe_path.get(), "rb") as f:
                head = f.read(65536)
            size = os.path.getsize(self.exe_path.get())
        except OSError:
            return False
        return head == self.data[:65536] and size == len(self.data)

    @staticmethod
    def _same_file(a, b):
        try:
            return filecmp.cmp(a, b, shallow=False)
        except OSError:
            return False

    def _current_offsets(self):
        return {f"0X{r['off']:08X}" for r in self.regions}

    @staticmethod
    def _dump_offsets(folder):
        index = folder / "index.txt"
        if not index.exists():
            return None
        try:
            lines = index.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        return {ln.split("|")[0].strip().upper() for ln in lines if "|" in ln}

    def original_dump(self):
        """(folder, note) holding the true original texts for this exe, or (None, "")."""
        meta = load_original_meta()
        if meta.get("fp") == fingerprint(self.data) and (ORIGINAL_DUMP / "index.txt").exists():
            return ORIGINAL_DUMP, ""
        # Older backups have no designated original: adopt the oldest dump, but
        # only if it belongs to this exact build (same prompt layout).
        for folder in sorted(BACKUP_DIR.glob("dump-*"), key=lambda p: p.name):
            if not folder.is_dir() or folder == ORIGINAL_DUMP:
                continue
            offs = self._dump_offsets(folder)
            if offs and offs == self._current_offsets():
                try:
                    shutil.copytree(folder, ORIGINAL_DUMP, dirs_exist_ok=True)
                    save_original_meta(fingerprint(self.data))
                except OSError:
                    return None, ""
                return ORIGINAL_DUMP, f"  (adopted from {folder.name})"
            continue  # oldest snapshot may belong to a different build; keep looking
        return None, ""

    def _original_exe(self):
        """(path, note) of the untouched copy of this exe, or (None, "")."""
        meta = load_original_meta()
        if meta.get("fp") == fingerprint(self.data) and ORIGINAL_EXE.exists():
            return ORIGINAL_EXE, ""
        for cand in sorted(BACKUP_DIR.glob("opencode-*.exe"), key=lambda p: p.name):
            if cand == ORIGINAL_EXE:
                continue
            if self._compatible_backup(cand):
                try:
                    shutil.copy2(cand, ORIGINAL_EXE)
                    save_original_meta(fingerprint(self.data))
                except OSError:
                    return None, ""
                return ORIGINAL_EXE, f"  (adopted from {cand.name})"
            continue  # oldest backup may belong to a different build; keep looking
        return None, ""

    def _compatible_backup(self, path):
        """A backup can only be the original of this build if its prompt
        layout matches the loaded file exactly."""
        try:
            if path.stat().st_size != len(self.data):
                return False
            data = path.read_bytes()
        except OSError:
            return False
        return {f"0X{r['off']:08X}" for r in scan(data)} == self._current_offsets()

    def apply(self, region, payload, done_text):
        if not self._exe_current():
            messagebox.showerror(
                "File changed",
                "The file at this path is not the one that was loaded.\n\n"
                "Press Reload first. Nothing was written.")
            self.status.config(text="Nothing written — press Reload.")
            return False
        try:
            ok, err = patch_bytes(Path(self.exe_path.get()), region, payload)
        except OSError as exc:
            messagebox.showerror("Failed", f"Nothing was written.\n\n{exc}")
            self.status.config(text="Nothing was written.")
            return False
        if not ok:
            if err == "too long":
                messagebox.showwarning(
                    "Text is too long",
                    "That text is longer than the space available for this prompt.\n\n"
                    "Shorten it a little and try again.")
            else:
                messagebox.showerror("Failed", err)
            return False
        self._mid_apply = True
        try:
            self.reload()
        finally:
            self._mid_apply = False
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
            self._too_long_warning(r, text, payload)
            return
        if not messagebox.askyesno(
                "Replace prompt",
                f'Replace "{r["name"]}" ({len(text):,} characters) with the contents of:\n\n'
                f"{path}\n\n"
                "Shorter text is fine — the leftover space is padded with blanks.\n"
                "A backup is saved first."):
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
        if self.is_pristine():
            messagebox.showinfo("Restore", "This file is still the original — nothing to restore.")
            return

        dump, _note = self.original_dump()
        if dump is None:
            messagebox.showinfo(
                "Restore",
                "No original dump exists for this file yet.\n\n"
                "backups\\dump-original is written the first time a change is made "
                "while the file is still untouched.")
            return

        match = None
        want = f"0X{r['off']:08X}"
        for line in (dump / "index.txt").read_text(encoding="utf-8").splitlines():
            off, _, name = line.partition("|")
            if off.strip().upper() == want:
                match = name.strip()
                break

        if not match:
            messagebox.showinfo("Restore", "This prompt is not in the original dump.")
            return

        src = dump / match
        if not src.exists():
            messagebox.showerror("Restore", "The saved copy of this prompt is missing.")
            return

        text = clean_text(src.read_bytes().decode("utf-8", "replace"))
        if clean_text(r["text"]) == text:
            messagebox.showinfo("Restore", f'"{r["name"]}" is already at its original text.')
            return

        if not messagebox.askyesno("Restore prompt",
                                   f'Restore "{r["name"]}" to the original text?'):
            return
        if self.backup():
            self.apply(r, encode_text(text, r["enc"]), f'Restored "{r["name"]}" to the original.')

    def restore_all(self):
        exe = Path(self.exe_path.get())
        src, _note = self._original_exe()
        if src is None:
            messagebox.showinfo(
                "Restore",
                "No original copy of this file exists yet.\n\n"
                "backups\\opencode-original.exe is saved the first time a change "
                "is made while the file is still untouched.")
            return

        if self._same_file(exe, src):
            messagebox.showinfo("Restore all", "This file is already the original.")
            return

        if not messagebox.askyesno("Restore all", "Restore the whole file to the original?"):
            return

        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            write_dump(self.regions, self.data, BACKUP_DIR / f"dump-{ts}")
            shutil.copy2(exe, BACKUP_DIR / f"opencode-{ts}.exe")
            shutil.copy2(src, exe)
        except OSError as exc:
            messagebox.showerror("Restore failed", f"Is OpenCode still running?\n\n{exc}")
            self.status.config(text="Restore failed.")
            return

        self._mid_apply = True
        try:
            self.reload()
        finally:
            self._mid_apply = False
        self.status.config(text="Restored the original version.")


if __name__ == "__main__":
    App().mainloop()
