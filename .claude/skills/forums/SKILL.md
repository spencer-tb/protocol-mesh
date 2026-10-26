---
name: forums
description: Search ethresear.ch and Ethereum Magicians and read whole threads as text. Use for research posts and for an EIP's discussion thread, its discussions-to link.
---

# Forums

Both are Discourse sites with the same JSON API, no login needed.

| Site | Holds |
|---|---|
| https://ethereum-magicians.org | EIP discussion threads |
| https://ethresear.ch | research posts |

## Search

```
curl -s 'https://ethereum-magicians.org/search.json?q=eip-8037' \
  | jq -r '.topics[] | "\(.id) \(.title)"'
curl -s 'https://ethresear.ch/search.json?q=state%20creation%20gas' \
  | jq -r '.topics[] | "\(.id) \(.title)"'
```

## Read a thread

`/raw/<topic id>` returns every post as plain text, which is the easiest
to read:

```
curl -s https://ethereum-magicians.org/raw/25694
```

`/t/<topic id>.json` has metadata such as `posts_count`, but only the first
20 posts.

Cite the site, the thread and the author of the post.
