---
name: eip
description: Look up an EIP fast, its spec text, fork inclusion status, recent changes, open PRs, discussion thread and where it is implemented and tested. Use whenever a task names an EIP number.
---

# EIP lookup

Paths are relative to the protocol-mesh directory. Replace `N` with the number.

## Spec text

`repos/EIPs/EIPS/eip-N.md`. The front matter has `status`, `requires`
and `discussions-to`. If the file is missing or stale, the EIP is newer
than the checkout: read https://eips.ethereum.org/EIPS/eip-N or
https://forkcast.org/eips/N.md.

## Fork status

```
curl -s https://forkcast.org/api/eips/N.json | jq '{title, status, reviewer,
  forks: [.forkRelationships[] | {forkName, champions, now: .statusHistory[-1]}]}'
```

Stages are Proposed, Considered, Scheduled, Included and Declined. Each
`statusHistory` entry names the call that decided it, for example
`acde/236`, which the `calls` skill can open. `laymanDescription`,
`benefits` and `tradeoffs` are plain-language prose. `reviewer: bot` means
no human has checked it.

What moved lately across all EIPs:
`curl -s https://forkcast.org/api/eip-stage-changes.json | jq '.eips[:20]'`.

## Changes and open PRs

```
git -C repos/EIPs log --oneline -10 -- EIPS/eip-N.md
gh search prs "EIP-N" --repo ethereum/EIPs --state open
```

## Discussion

Open the `discussions-to` link with the `forums` skill. Call decisions and
mentions are in the `calls` skill, Discord channels in the `discord` skill.

## Implementation and tests

```
rg -l "EIP-N" repos/execution-specs/src
ls -d repos/execution-specs/tests/*/eipN_*
rg -il "eip-?N" repos/clients/<client>
gh search prs "EIP-N" --repo ethereum/execution-specs --state open
```
