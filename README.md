# Opencode-SystemPrompt manager

Read, replace, or clear the system prompts compiled into `opencode.exe`.

- **Custom prompt** — swap a model's built-in instructions for your own.
- **Clear the prompt** — blank it out so fewer tokens go into every request. Useful if you only want your own `AGENTS.md` instructions.

![Opencode-SystemPrompt manager](screenshot.png)

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
> The app looks for `opencode.exe` next to itself first, so the installed one under `%APPDATA%\npm\...` is never touched. Keep the original somewhere safe.

## Quick start

```text
1. Copy opencode.exe into this folder
2. Run OpenCode Prompt Manager.exe
3. Pick a prompt, then use the buttons below
```

## Tabs

| Tab | Contents |
|---|---|
| **Prompts** | Every prompt found in the file, with its embedded path, size, and preview |
| **Model prompts** | Which prompt each model family receives (default, GPT, Kimi, Meta, Trinity, Claude) — double-click a row to copy its embedded path |

## Buttons

| Button | What it does |
|---|---|
| **Dump prompts** | Saves every prompt as `.txt` into `backups\manual-<time>\` |
| **Replace selected** | Replaces the selected prompt with a text file you choose |
| **Clear selected** | Blanks the selected prompt |
| **Restore selected** | Puts the last saved version of that one prompt back |
| **Restore all** | Puts the whole exe back to the last saved backup |

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
> - A replacement must **fit in the space the original used**. The app tells you if it is too long — shorten it.
> - Every change saves a full copy of the exe plus a text dump under `backups\` first.
> - OpenCode must be closed while changing the file.

## Files

```text
ocprompt.pyw                  source
OpenCode Prompt Manager.exe   the app
regions.json                  cached prompt locations for the loaded exe
backups\                      automatic backups and dumps
```
