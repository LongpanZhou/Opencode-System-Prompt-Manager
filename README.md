# Opencode-SystemPrompt manager

Read, replace, or clear the system prompts compiled into `opencode.exe`.

![Opencode-SystemPrompt manager](screenshot.png)

> [!WARNING]
> This tool edits a program made by someone else: it may violate OpenCode's terms, can break the software, and patches vanish when OpenCode updates. Use it on a copy, keep backups, never redistribute a modified `opencode.exe`, and accept the risk.

## Quick start

```text
1. Copy opencode.exe into this folder
2. Run OpenCode Prompt Manager.exe
3. Pick a prompt, edit the preview box, press Save (or Ctrl+S)
4. Happy? Apply to installed so real `opencode` runs use it too
```

## Tabs and buttons

- **Prompts** — every prompt in the file with its size and an editable preview box. Shorter messages are fine: the leftover space is padded automatically and never shown as part of your text. A replacement can only be as long as the prompt it replaces — need more room? Set `system` per agent in `opencode.jsonc`, which has no length limit.
- **Model prompts** — which model family receives which prompt. The **Stored prompt** column names the very same prompt as in the Prompts tab; clearing a row clears those same bytes. Right-click a row to jump to it.
- **Dump prompts** — saves every prompt as `.txt` under `backups\`.
- **Replace selected** — swaps a prompt for a text file you choose. **Clear selected** — blanks it.
- **Restore selected / Restore all** — always go back to the **true original**, never "the last change". The original texts and a pristine exe copy are saved under `backups\` the first time an untouched file is changed; if they are missing, the oldest matching backup is adopted as the original.

## Which file gets changed?

The app edits the file shown in **OpenCode file** — by default your copy in this folder; the installed exe under `%APPDATA%\npm\...` is only touched by **Apply to installed**, which backs it up first.

There is no separate "bun server" behind OpenCode: `opencode.exe` is a single Bun executable with everything bundled inside, prompts included. Whatever exe actually runs is the one whose prompts are used — an npm update replaces the installed exe with a fresh, unmodified one, so keep your edited copy here and hit **Apply to installed** again after updating. Close OpenCode while changing the file.

## Files

```text
ocprompt.pyw                  source
OpenCode Prompt Manager.exe   the app
regions.json                  cached prompt locations (not committed)
backups\                      originals, per-change backups and dumps
```
