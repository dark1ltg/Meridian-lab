# Finish populating Meridian-lab (one command)

Cloud Agent cannot `git push` to this repo (Cursor GitHub App has no write access on Meridian-lab).
GitHub MCP can write files as your user, but uploading the full tree + binaries through that API is slow.

From any machine where you are logged into GitHub as **dark1ltg**:

```bash
git clone --bare https://github.com/dark1ltg/Meridian.git /tmp/Meridian.git
cd /tmp/Meridian.git
git push --mirror https://github.com/dark1ltg/Meridian-lab.git
```

Then replace the lab README banner (mirror overwrites it):

```bash
git clone https://github.com/dark1ltg/Meridian-lab.git
cd Meridian-lab
# prepend the experimental banner to README.md, commit, push
```

Or grant the **Cursor** GitHub App **Contents: Read and write** on `dark1ltg/Meridian-lab`, then ask the agent to finish the mirror.
