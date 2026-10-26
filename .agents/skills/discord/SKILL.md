---
name: discord
description: Search the Eth R&D Discord archive for what researchers and client developers said about an EIP or topic. Use when a task needs Discord discussion, since Discord links themselves cannot be opened.
---

# Eth R&D Discord archive

`repos/eth-rnd-archive` is a snapshot that EF DevOps updates weekly, so
the last few days may be missing.

- One folder per channel: EIP channels such as `8037-state-creat-gas` and
  `8141-frames`, call channels such as `ACDE_247`, and topic channels.
- One file per active day, `YYYY-MM-DD.json`, holding a list of messages
  with `author`, `category`, `parent` (thread), `content` and `created_at`.
- Mentions are raw Discord ids, GIFs and reactions are missing, and
  attachments are Git LFS files that the setup does not download.

## Recipes

```
cd repos/eth-rnd-archive
ls | rg -i frames                                  # find a channel
rg -il "inclusion list" --glob '*.json' | sort     # which channels and days
jq -r '.[] | "\(.created_at[:16]) \(.author): \(.content)"' 8141-frames/2026-10-2*.json
jq -r '.[] | select(.author == "<name>") | "\(.created_at[:16]) \(.content)"' 8141-frames/*.json
```

Cite the channel, the date and the author.
