# Opencode-SystemPrompt manager

Read, replace, or clear the system prompts compiled into `opencode.exe`.

- **Custom prompt** — swap a model's built-in instructions for your own.
- **Clear the prompt** — blank it out so fewer tokens go into every request. Useful if you only want your own `AGENTS.md` instructions.

![Opencode-SystemPrompt manager](screenshot.png)

**[Download the latest release](https://github.com/LongpanZhou/Opencode-System-Prompt-Manager/releases/latest)** — Windows x64, self-contained.

> [!WARNING]
> This tool edits a program made by someone else.
>
> - **May violate OpenCode's terms.** Modifying their binary is not supported by the OpenCode team, and their terms may prohibit it.
> - **Can break the software.** A bad prompt or corrupted write can make `opencode.exe` crash, fail to start, send broken requests, or make a model behave nothing like it should.
> - **Can break updates.** A new install replaces the patched file, so your changes vanish.
> - **No support.** If the app misbehaves afterward, it is not the OpenCode team's bug.
> - **Never redistribute** a modified `opencode.exe`.
>
> Use it on a copy, keep backups, accept the risk.

## Before you start

> [!IMPORTANT]
> **Copy your own `opencode.exe` into this folder and use that copy.**
>
> The app looks for `opencode.exe` next to itself first, so experiments normally never touch the installed one under `%APPDATA%\npm\...`. If no copy sits next to the app, though, it edits the installed exe directly — keep a copy in this folder.

## Quick start

```text
1. Copy opencode.exe into this folder
2. Run OpenCode Prompt Manager.exe
3. Pick a prompt, then use the buttons below
4. Happy with it? Use **Apply to installed** so real `opencode` runs use it too
```

## Which file gets changed?

The app edits the file shown in **OpenCode file** — by default your copy in this folder; the installed exe under `%APPDATA%\npm\...` is only touched by **Apply to installed** (or if you point the app at it yourself).

There is no separate "bun server" behind OpenCode: `opencode.exe` is a single Bun executable with everything bundled inside, prompts included. Whatever exe actually runs is the one whose prompts are used — so changes only take effect in real runs once they are in that exe. Keep your edited copy here as the source of truth: an npm update replaces the installed exe with a fresh, unmodified one, and **Apply to installed** puts your version back.

## Tabs

| Tab | Contents |
|---|---|
| **Prompts** | Every prompt found in the file, with its embedded path, size, and preview |
| **Model prompts** | Which prompt each model family receives (default, GPT, Kimi, Meta, Trinity, Claude). The **Stored prompt** column names the very same prompt you see in the Prompts tab — clearing a row clears those same bytes. Double-click copies the embedded path; right-click jumps to the prompt. |

## Buttons

| Button | What it does |
|---|---|
| **Dump prompts** | Saves every prompt as `.txt` into `backups\manual-<time>\` |
| **Replace selected** | Replaces the selected prompt with a text file you choose |
| **Clear selected** | Blanks the selected prompt |
| **Restore selected** | Puts that one prompt back to its original text |
| **Restore all** | Puts the whole exe back to the original copy |

Both preview boxes are **editable** — type or paste a message, then press **Save** (or Ctrl+S) next to the box to write it back into the exe. Copying works the same way as before: select text (or press Ctrl+A), then Ctrl+C or right-click → Copy.

## The original backup

Restore never goes back to "the last change" — it always restores the **true original**:

- `backups\dump-original\` — every prompt exactly as it was before the first change.
- `backups\opencode-original.exe` — the untouched exe.

Both are written the first time the app changes a file that is still unmodified, and they are refreshed if you load a different (fresh) build. If they do not exist yet but older `backups\dump-*` / `opencode-*.exe` copies do, the oldest of them is adopted as the original — only when its prompt layout matches the loaded file.

## How OpenCode uses these prompts

The prompts are stored as plain text in the exe. No role is stored with them — the server assigns a role when it builds a request.

| Prompt | Role | Where it goes |
|---|---|---|
| Default / GPT / GPT-Astra / Kimi / Meta / Trinity | `system` | `system[0]`, picked by model family |
| Claude style fragment | `system` | extra part, joined into the same system message |
| File search agent | `system` | the subagent's own session |
| Code reviewer | `user` | sent as your message when you run `/review` |
| `patch`, `todowrite`, `snapshot`, `initialize` | — | `tools[].description`, never a message |

Each request sends:

```text
system:  [ prompt ] + [ environment block ]   -> joined with "\n", one system message
messages: full conversation
tools:    tool definitions
```

> [!NOTE]
> - The environment block (cwd, platform, date, tool list) is generated per request. It is **not** in the exe, so replacing a prompt does not remove it.
> - An agent with `system` set in `opencode.jsonc` replaces the prompt entirely; the exe blob is never read.
> - Some OpenAI-compatible routes fold the system text into the first user message instead.
> - The prompt is re-sent on **every** request, including each step of a tool loop — so clearing it saves tokens repeatedly, not once.

## Limits

> [!NOTE]
> - A replacement must **fit in the space the original used**. The app tells you if it is too long — shorten it. Anything shorter is fine: the leftover space is padded with blanks, and the preview and copies show your text without that padding.
> - Type directly in the preview boxes and press **Save** to write your message back; no separate text file needed.
> - Need a much longer prompt? OpenCode ignores the embedded prompt entirely when an agent sets `system` in `opencode.jsonc` — no length limit that way.
> - Every change saves a full copy of the exe plus a text dump under `backups\` first.
> - OpenCode must be closed while changing the file.

## Files

```text
ocprompt.pyw                  source
OpenCode Prompt Manager.exe   the app
regions.json                  cached prompt locations for the loaded exe
backups\dump-original\        the true original prompt texts
backups\opencode-original.exe the untouched exe copy
backups\original.json         which build the original pair belongs to
backups\dump-<time>\          text dump taken right before each change
backups\opencode-<time>.exe   full exe copy taken right before each change
```
