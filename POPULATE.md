# Meridian-lab setup (separate repo + separate Cursor workspace)

`dark1ltg/Meridian-lab` is the experimental fork. Stable product stays in `dark1ltg/Meridian`.

## 1. Finish the repo copy (one command)

From a machine logged in as **dark1ltg**:

```bash
git clone --bare https://github.com/dark1ltg/Meridian.git /tmp/Meridian.git
cd /tmp/Meridian.git
git push --mirror https://github.com/dark1ltg/Meridian-lab.git
```

That copies full history, fonts, and screenshots. (This Cloud Agent cannot `git push` here.)

## 2. Open a NEW Cursor workspace (required)

Do **not** keep experimenting inside the Meridian workspace. This agent session is bound to Meridian and cannot switch.

- **Desktop:** `git clone https://github.com/dark1ltg/Meridian-lab.git` → Cursor → **Open Folder** on that clone (new window).
- **Cloud Agent:** start a **new** agent with repository `dark1ltg/Meridian-lab`.

Optional: after the mirror, re-add the experimental README banner and commit on Meridian-lab only.
