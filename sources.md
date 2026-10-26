# Sources

Where the context for EIP work lives. Repos are cloned by `setup.sh` (see
`repos.txt`), sites are fetched when needed. Each has a skill in
`.agents/skills/` with the exact commands.

## Cloned

| Source | Repo | Skill |
|---|---|---|
| EIP text | `EIPs` | `eip` |
| Spec and tests | `execution-specs`, `execution-apis` | `eip` |
| Call notes, chat, raw transcripts | `ethereum-pm` | `calls` |
| Call summaries, decisions, corrected transcripts | `forkcast` | `calls` |
| Eth R&D Discord, weekly snapshot | `eth-rnd-archive` | `discord` |
| Devnet configs | `<fork>-devnets`, `hive-tests` | `devnet` |
| Local devnets | `ethereum-package` | `kurtosis` |
| Hive | `hive`, `clive` | `hive` |

## Fetched

| Site | Use | Skill |
|---|---|---|
| https://forkcast.org/llms.txt | JSON index of EIPs, upgrades and calls, built for agents | `eip`, `calls` |
| https://eips.ethereum.org | rendered EIPs, newest text | `eip` |
| https://ethereum-magicians.org | EIP discussion threads, JSON API | `forums` |
| https://ethresear.ch | research posts, JSON API | `forums` |
| https://hive.ethpandaops.io | CI hive results, through `hapi` and `clive` | `hive` |

## Optional

- [panda](https://github.com/ethpandaops/panda): CLI and MCP server for
  devnet metrics, logs and nodes. Needs ethPandaOps access.

## Not usable by agents

Discord links need a login, HackMD notes change or vanish, and recordings
have no text. Use the archive, the repos and the transcripts above instead.
