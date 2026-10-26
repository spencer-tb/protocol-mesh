---
name: calls
description: Find what was said or decided on AllCoreDevs and breakout calls, using summaries, key decisions, EIP mentions, chat logs and transcripts. Use for "what did ACDE decide about X", "when was X discussed" and agenda or action item questions.
---

# Calls

Paths are relative to the protocol-mesh directory.

Prefer summaries and decisions over transcripts. Transcripts are people
thinking out loud, so quote them only as "said on the call", with the call
and timestamp.

## Across all calls

Forkcast has every call's highlights and decisions in one file:

```
curl -s https://forkcast.org/search-light.json > /tmp/forkcast-calls.json
jq -r --arg q "8037" '.calls[] | . as $c | .entries[]
  | select(.text | test($q; "i"))
  | "\($c.path) \($c.date) \(.timestamp) [\(.kind)] \(.text | gsub("\n"; " "))"' \
  /tmp/forkcast-calls.json
```

The call list with series names: `curl -s https://forkcast.org/api/calls.json
| jq '.series, .calls[-10:]'`.

## One call

Each call has a folder `<series>/<date>_<number>`, for example
`acde/2026-10-22_248`. Series include acde, acdc, acdt, bal, focil, epbs and
zkevm. The two repos use slightly different series names, so list the
folder.

| Folder | Files |
|---|---|
| `repos/forkcast/public/artifacts/<series>/<date>_<n>/` | `tldr.json`, `notes.json`, `key_decisions.json`, `eip_mentions.json`, `transcript_corrected.vtt` |
| `repos/ethereum-pm/.github/ACDbot/artifacts/<series>/<date>_<n>/` | `chat.txt` (Zoom chat), `tldr.json`, `transcript.vtt`, `transcript_corrected.vtt` |

`key_decisions.json` holds `.key_decisions[]` with `original_text`,
`timestamp`, `type` and `eips` (numbers):

```
cd repos/forkcast/public/artifacts
for f in */*/key_decisions.json; do
  jq -r --arg f "$f" '.key_decisions[] | select(.eips | index(8037))
    | "\($f) \(.timestamp) \(.original_text)"' "$f"
done
rg -i "frame transaction" */*/transcript_corrected.vtt
```

## Notes, agendas and what is next

- Written notes: `repos/ethereum-pm/AllCoreDevs-EL-Meetings/`,
  `AllCoreDevs-CL-Meetings/`, `Breakout-Room-Meetings/`.
- Agendas are issues: `gh issue list -R ethereum/pm --search "ACDE" --state all`.
- Upcoming agenda suggestions, deferred decisions and open action items:
  `repos/forkcast/public/artifacts/<series>/plan/`.
