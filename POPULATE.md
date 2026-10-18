# Meridian-lab setup (separate repo + separate Cursor workspace)

`dark1ltg/Meridian-lab` is the experimental fork. Stable product stays in `dark1ltg/Meridian`.

## 1. File tree

The lab working tree already matches `dark1ltg/Meridian` at `b48f89d` (application source, tests, packaging, fonts, and screenshots). History was not mirrored; lab commits stay on `main`.

To replace this repo with a full history mirror later, from a machine logged in as **dark1ltg**:

```bash
git clone --bare https://github.com/dark1ltg/Meridian.git /tmp/Meridian.git
cd /tmp/Meridian.git
git push --mirror https://github.com/dark1ltg/Meridian-lab.git
```

That overwrites lab history. Re-apply the experimental README banner afterward.

## 2. Open a NEW Cursor workspace (required)

Do **not** keep experimenting inside the Meridian workspace. This agent session is bound to Meridian and cannot switch.

- **Desktop:** `git clone https://github.com/dark1ltg/Meridian-lab.git` → Cursor → **Open Folder** on that clone (new window).
- **Cloud Agent:** start a **new** agent with repository `dark1ltg/Meridian-lab`.

Optional: after the mirror, re-add the experimental README banner and commit on Meridian-lab only.
