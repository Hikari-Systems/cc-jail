# The example ACL

`acl.json` grants one user, `graphiti`, read and write on one graph, also called
`graphiti`. Its password is the argon2id hash of **`graphiti-example`** — a throwaway,
published here on purpose so the example runs with no setup.

**Change it before this is reachable by anything but you.** The stack binds Slater to
`127.0.0.1:7687` on your machine, which is why a published password is acceptable for a
local try-out and not for anything else.

To change it, mint a new hash and paste it in, then set the same plaintext as
`SLATER_PASSWORD` in `.env`:

```sh
docker run --rm --entrypoint /app/slater hikarisystems/slater:v0.25.2 \
  hash-password 'your-password'
```

The hash is salted per invocation, so the same password hashes differently every time —
two different-looking hashes here are not a mistake.

## Why the graph carries the ACL's fingerprint

`slater-build` is run with `--acl`, which stamps this file's BLAKE3 into the generation's
manifest. A server configured with an ACL refuses a generation stamped against a
different one, so a graph and the ACL it was built against travel together and cannot be
silently mismatched.

The practical consequence: **edit this file and the graph must be rebuilt.** On a graph that
holds nothing yet that costs one command and nothing else:

```sh
docker compose --profile seed run --rm slater-seed
```

On one already in use it is not that cheap — rebuilding discards the write delta, and
everything the jail has remembered with it. See "Memory" in the top-level README.md.

> Note: this file came from graphiti-slater's `docker-example`, where the seed runs as
> `docker compose up slater-seed`. In cc-jail it sits behind the `seed` profile, and `up`
> must never reach it — the command above is the one to use here.
