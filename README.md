# cc-jail (Jail for Claude Code)

A containerised [Claude Code](https://claude.com/claude-code) environment. Claude Code runs inside
an Ubuntu container as an unprivileged `claude` user, with your source tree bind-mounted in — so
the agent sees only the directories you explicitly mount, and its credentials and config live in
this repo rather than on your host.

The container also gets a Docker CLI wired to the **host's** daemon, so Claude can build and run
containers as part of its work. See [Docker access](#docker-access) — this is convenient, and it
is also the one thing that punches a hole through the isolation above.

## Quick start

```bash
git clone <repo-url> cc-jail && cd cc-jail
docker compose run --rm claude
```

If the host's docker socket group doesn't happen to match the image's, use this instead — it reads
the GID off the socket and passes it in ([why](#docker-socket-group)):

```bash
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%g' /var/run/docker.sock) \
  docker compose run --rm claude
```

Either way you land in Claude Code itself, running in `/workspace`. The first invocation walks you
through logging in; credentials are written to `.claude/` in this repo, so later runs start
straight up. Name a command to get something else instead — `docker compose run --rm claude bash`
for a shell, with `claude` on the `PATH` there too.

Give it a git identity and an ssh agent once and it can commit and push too — see
[Git identity](#git-identity) and [SSH agent](#ssh-agent).

Nothing else needs setting up — the mount points ship with the repo, and `--rm` means each session
leaves no stopped container behind. If you want the agent making commits, give it a git identity
once: see [Git identity](#git-identity).

By default `/workspace` is `./cc-jail`, an empty directory in this repo, which is a deliberately
useless place to work. Point it at your actual code with `WORKSPACE_HOST`:

```bash
WORKSPACE_HOST=~/git/projects docker compose run --rm claude
```

## Options

All optional. Set them in `.env`, which Compose reads automatically:

```bash
cp .env.example .env
```

…or prefix a single invocation, as above — the shell environment always wins over `.env`.

| Variable         | Meaning                                          | Default      |
| ---------------- | ------------------------------------------------ | ------------ |
| `WORKSPACE_HOST` | Host directory to mount in                       | `./cc-jail`  |
| `WORKSPACE_PATH` | Where it appears inside the container            | `/workspace` |
| `UID` / `GID`    | Owner of files written back to the host (Linux)  | `1000`       |
| `DOCKER_GID`     | GID owning `/var/run/docker.sock` on the host    | `999`        |

### Passing host environment variables

To pass all exported variables from the host into a one-off container, use Compose's
`--env-from-file` with shell process substitution:

```bash
docker compose run --rm --env-from-file <(env) claude
```

Only exported variables are included. Variables that contain secrets are also forwarded, so a
safer option is to pass only the variables Claude needs:

```bash
docker compose run --rm \
  -e ANTHROPIC_API_KEY \
  -e AWS_PROFILE \
  -e GITHUB_TOKEN \
  claude
```

With `-e NAME` and no value, Compose reads `NAME` from the host environment.

Either form outranks the `environment:` block in `docker-compose.yml`, so forwarding wholesale would
otherwise overwrite the paths this setup depends on — `HOME` above all, which the host always sets
and which points at a directory that does not exist in here. The service entrypoint re-exports
`HOME`, `CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME`, `XDG_RUNTIME_DIR` and `SSH_AUTH_SOCK` inside the
container, after any `-e` has been applied, so those five survive.

### Where your code is mounted

`WORKSPACE_HOST` is the one that matters. Anything **not** mounted is invisible to the agent —
that's the point: mount the projects you're working on, and nothing else.

`WORKSPACE_PATH` decides where that tree appears inside the container. Mounting it at the *same*
path on both sides is worth doing if Claude will run `docker` itself — see
[Docker access](#docker-access) for why:

```dotenv
WORKSPACE_HOST=/home/you/git/projects
WORKSPACE_PATH=/home/you/git/projects
```

### Docker socket group

`/var/run/docker.sock` is owned by a group whose GID varies by platform — commonly `999` on plain
Ubuntu/Debian, `1001` under Docker Desktop / WSL2, `0` on macOS. Read it off the socket rather than
guessing:

```bash
stat -c '%g' /var/run/docker.sock
```

Compose uses that GID twice. At build time it names the group inside the image and adds `claude` to
it. At run time it goes to `group_add`, and *that* is what grants access to the mounted socket —
the socket belongs to the host, so its group can't be settled at build time. A wrong `DOCKER_GID`
is therefore fixed by the next `run`, with no rebuild.

Compose itself has no command substitution — `${DOCKER_GID:-999}` can fall back to a literal, never
to a command — so the lookup has to happen in the shell. Worth a function if you use this often:

```bash
ccjail() {
  ( cd ~/git/projects/cc-jail \
    && DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) \
       docker compose run --rm claude "${@:-bash}" )
}
```

`ccjail` for a shell, `ccjail claude` to go straight into the agent. The subshell keeps the `cd`
from leaking into your session. To check the wiring:

```bash
docker compose run --rm claude docker ps    # should list the host's containers
```

A permission-denied there means `DOCKER_GID` doesn't match the socket.

### File ownership on Linux

Set `UID`/`GID` to your own (`id -u`, `id -g`) so files the container writes back are owned by you.
`1000` is already correct for most single-user installs. On macOS, Docker Desktop handles ownership
itself and you can leave them alone. Changing these does need a rebuild — they're build args, not
runtime values.

### Mounting more than one directory

Compose can't build a variable-length list of mounts from environment variables, so for a second
project create a `docker-compose.override.yml` — Compose merges it in automatically and volume
lists are appended:

```yaml
services:
  claude:
    volumes:
      - ${HOME}/git/other:/workspace/other
```

## Git identity

The container starts with no git config of its own, so commits made in here would fail with
*"Please tell me who you are"* — and a `git config --global` fixing that would vanish with the
container. `.config/` in this repo is mounted at `/home/claude/.config` to hold it, the same way
`.claude/` holds Claude Code's state. `XDG_CONFIG_HOME` points there, and git reads its global
config from `$XDG_CONFIG_HOME/git/config` whenever `~/.gitconfig` is absent, which in here it
always is.

Seed it once, from the host, with your own config:

```bash
cp ~/.gitconfig .config/git/config
```

…or write just what you want the agent committing as:

```bash
cat > .config/git/config <<'EOF'
[user]
	name = Your Name
	email = you@example.com
[init]
	defaultBranch = main
EOF
```

From then on it persists, and `git config --global …` **inside** the container edits that same
file — the change is on your host the moment it is made.

`.config/git/` is already there on a fresh clone, like every other directory this setup writes
into — nothing to create first.

The contents are gitignored (`.config/*`, `.config/git/*`), like `.claude/`. Keep it that way: a
git identity is personal, and anything else that lands in there — `gh`'s OAuth token in
`.config/gh/hosts.yml`, for instance — is a live credential.

Two things worth knowing when copying a host config wholesale:

- **Helpers must exist in the container.** A `[filter "lfs"]` block, a `credential.helper`
  pointing at Windows' `git-credential-manager.exe`, a `gpg.program` — git will run these and fail
  when they aren't installed. Either drop those sections from the copy, or add the package to the
  `Dockerfile` (`git-lfs` and `gnupg` are one `apt-get install` line).
- **Paths are container paths.** `core.excludesfile = /home/you/.gitignore_global` resolves inside
  the container, where that file doesn't exist.

It has to be a directory mount rather than a `./gitconfig:/home/claude/.gitconfig` file mount:
`git config` rewrites the file by renaming a lock file over it, and renaming over a bind-mounted
*file* fails with `Device or resource busy`. Reads would work; the first write would not.

## SSH agent

Pushing over ssh needs a key, and putting one in the container would undo the point of the
container. The host's **ssh-agent socket** is mounted instead, at `/run/ssh-agent.sock`, with
`SSH_AUTH_SOCK` pointed at it. The agent signs on the container's behalf; no private key ever
crosses the boundary, and nothing persists if you stop mounting it.

It works as soon as the host has an agent running — `${SSH_AUTH_SOCK}` in `docker-compose.yml`
reads the host's own value:

```bash
ssh-add -l    # on the host: lists the keys the container will be able to use
```

Inside, `ssh -T git@github.com` should greet you by name. If it doesn't, check `ssh-add -l` on the
host first; an empty agent forwards nothing. With no agent running at all, the mount falls back to
`/dev/null` and ssh reports `Error connecting to agent: Connection refused` — everything else still
works.

`.ssh/` in this repo is mounted at `/home/claude/.ssh`, which is where ssh writes `known_hosts` on
first connection and where an ssh `config` goes if you want one — so a host key accepted once is
still accepted next run. Like the other mounts it ships as `.gitkeep` and its contents are
gitignored: no keys, no `known_hosts`, nothing personal heading for a remote.

**What this gives away.** Agent forwarding doesn't leak key material, but for as long as the
container runs it can ask the agent to sign *anything*, with *every* key the agent holds — pushes
to unrelated repositories, logins to other hosts. That is a weaker boundary than the rest of this
setup, though a much stronger one than mounting `~/.ssh`. Narrow it by loading only the key you
need (`ssh-add -D`, then add that one), or drop the socket line from `docker-compose.yml` and push
from the host.

## Docker access

`/var/run/docker.sock` is mounted in, and the container ships the Docker CLI, buildx and compose
plugins — but no daemon of its own. Commands run inside the container are executed by the host's
Docker daemon (docker-out-of-docker), so images you build and containers you start are siblings of
this one, visible from the host with `docker ps`.

Two consequences worth knowing:

**It is root-equivalent access to the host.** Anything that can talk to the daemon socket can start
a privileged container mounting `/`, which is a complete bypass of the filesystem isolation this
setup otherwise gives you. If you want the sandbox to actually hold, delete the socket line from
`docker-compose.yml`.

**Paths in `docker run -v` are host paths, not container paths.** The daemon resolves them on the
host, so `-v /workspace/foo:/src` from inside the container will not find your code — it looks for
`/workspace/foo` on the host. Mounting your tree at the same path on both sides (see
[Where your code is mounted](#where-your-code-is-mounted)) makes paths mean the same thing inside
and out.

## Memory

Optionally, the jail can come with a memory: [Graphiti](https://github.com/getzep/graphiti)'s
temporal knowledge graph, stored in [Slater](https://github.com/Hikari-Systems/slater) and exposed
to Claude Code as an MCP server, so a preference stated in one session is still known in the next.
The adapter that puts the one on the other is
[graphiti-slater](https://github.com/Hikari-Systems/graphiti-slater), and everything under
`memory/` is that project's runnable example, wired into this compose file.

```
┌────────────────┐   MCP over HTTP    ┌────────────┐   Bolt :7687   ┌────────┐
│  claude (jail) │ ─────────────────► │  graphiti  │ ─────────────► │ slater │
└────────────────┘  http://graphiti   └────────────┘                └────────┘
                         :8000/mcp
```

It sits behind a compose profile, so a plain `docker compose run --rm claude` is untouched by any
of it and still needs no credentials.

### What you need

- **An AWS account with Bedrock access**, able to invoke the model id in `memory/mcp/config.yaml`
  (`anthropic.claude-sonnet-4-6`) on-demand in your region. This is what extracts entities from
  what you tell it; there is no Anthropic API key involved, because the calls are signed as AWS
  requests.
- **A Voyage API key**, for embeddings.

Both go in `.env` — see the memory block at the bottom of `.env.example`.

### Starting it

Build the graph once, then bring the stack up:

```bash
docker compose --profile seed run --rm slater-seed     # ONCE -- see below
docker compose --profile memory up -d graphiti
docker compose run --rm claude
```

Run these three commands **from the host, not from inside the jail.** The jail's `docker` talks to
the host's daemon, so the bind mounts under `memory/` would be resolved as host paths and come up
empty — see [Docker access](#docker-access).

Then register the server with Claude Code, once, from inside the jail — see
[Registering it with Claude Code](#registering-it-with-claude-code).

### The MCP server

`graphiti` is Graphiti's own MCP server, unforked, speaking streamable HTTP. It offers nine tools:

| Tool | What it does |
| --- | --- |
| `add_memory` | The way in. Takes a `name` and an `episode_body`, plus an optional `source` of `text`, `message` or `json`. |
| `search_nodes` | Finds *entities* — the people, preferences, organisations and so on that were extracted. Optionally filtered by `entity_types`. |
| `search_memory_facts` | Finds *relationships* — the facts connecting those entities, each with the episode it came from. `center_node_uuid` re-ranks by distance from one node. |
| `get_episodes` | The raw episodes as they were submitted, most recent first. |
| `get_entity_edge` | One fact by uuid. |
| `delete_entity_edge` | Forgets one fact. See the note below — this one is expected to fail. |
| `delete_episode` | Forgets one episode by uuid. |
| `clear_graph` | Everything in the named groups, gone. |
| `get_status` | Whether the server is up and the graph reachable. The cheap first thing to try. |

Three things about it are worth knowing before you rely on it:

**`add_memory` returns before the memory exists.** It queues the episode and answers
`Episode 'x' queued for processing` straight away. The work — Claude extracting entities and
relationships, Voyage embedding them, Slater storing them — happens behind that, and episodes
sharing a `group_id` are processed one at a time so they cannot race. So a memory added and
immediately searched for may not be there yet; a few seconds later it is. Once it *is* written it
is searchable at once, with no consolidation step in between.

**Everything lands in one group.** `group_id: main`, from `memory/mcp/config.yaml`, is the default
for both writing and searching, so all sessions and all workspaces share a single memory. Pass an
explicit `group_id` to partition it — per project, say — remembering that the searches take
`group_ids` and default to `main` too.

**`delete_entity_edge` does not work, deliberately.** Slater refuses a keyed relationship delete,
and the keyless form it does accept cannot spare that edge's siblings — Graphiti writes several
facts between the same pair of entities. Failing loudly beats deleting the wrong facts, so the
adapter declines it and Graphiti's own statement runs and errors. `delete_episode` and
`clear_graph` both work.

Search is hybrid: BM25 full text and vector similarity, both served by Slater, fused into one
ranking. Entity extraction is typed — `Person`, `Preference`, `Requirement`, `Procedure`,
`Location`, `Event`, `Organization`, `Document`, `Topic`, `Object` — and those names are declared
in `memory/mcp/config.yaml`. Adding one to that list means rebuilding the graph, because a label
only enters Slater's symbol table through a seed row.

### Registering it with Claude Code

From inside the jail:

```bash
claude mcp add --transport http --scope user graphiti http://graphiti:8000/mcp
```

Once ever, not once per session. `--scope user` writes it to `.claude.json`, which
`CLAUDE_CONFIG_DIR` puts inside the `./.claude` bind mount — so it is on your host the moment it
is written and every later container starts with it already there. It also applies in every
working directory, which the default scope (`local`, keyed to one directory) does not, and it
keeps the entry out of your source tree, which `--scope project` would not: that one writes an
`.mcp.json` into `/workspace`, i.e. into whatever repository you mounted.

Check it:

```bash
claude mcp list          # graphiti ... ✓ Connected
```

…or `/mcp` inside a session, which lists the tools as well. To remove it:

```bash
claude mcp remove --scope user graphiti
```

`http://graphiti:8000` is the compose network's own name for the server, which is why this needs
no published port and does not change with `WORKSPACE_HOST`. It resolves only while the memory
stack is up; with the stack down, Claude Code reports `graphiti` as failing to connect and
otherwise runs normally.

> **The endpoint is `/mcp`, not `/mcp/` — and the server's own startup log tells you the wrong
> one.** It prints `MCP Endpoint: http://localhost:8000/mcp/`, but `/mcp/` answers `307` with a
> redirect to `/mcp` and some clients will not replay a POST across a redirect. If the server
> appears to connect and every call then fails, check this first.

#### From the host, too

Both services publish a loopback port, so a Claude Code running on the host — outside the jail —
can share the same memory:

```bash
claude mcp add --transport http --scope user graphiti http://localhost:8010/mcp
```

Bolt is on `127.0.0.1:7688` alongside it, for cypher-shell or any Neo4j driver. Both are moved off
their conventional numbers on purpose: 7687 and 8000 are the ports everything else on a developer
machine wants, and a collision would take the jail's memory down with it. `SLATER_BOLT_PORT` and
`GRAPHITI_PORT` move them again if even those are taken.

Use `localhost` and not `127.0.0.1` here. The server checks the `Host` header against an
allow-list that holds both, so either connects — but see below for what that allow-list costs from
inside the jail.

### Try it

Tell Claude something worth keeping:

> Remember that I prefer TypeScript over JavaScript for new projects, and that I work at Acme
> Corp.

Then, in a later session:

> What do you know about my preferences?

The first call extracts entities on Claude, embeds them on Voyage and persists nodes and edges
into Slater. The second searches. You can watch both happen:

```bash
docker compose --profile memory logs -f graphiti slater
```

### Why the server needed one patch

The stock server builds its `FastMCP` object with no `host` argument and assigns
`mcp.settings.host` from the config file afterwards. FastMCP auto-enables DNS-rebinding
protection whenever the *constructor* host is a loopback one — which the default `127.0.0.1`
is — and stamps in an allow-list of `localhost` and `127.0.0.1` only. Setting
`server.host: 0.0.0.0` later changes what the socket binds to and nothing else, so the server
listens everywhere and then answers `421 Invalid Host header` to anything that addressed it by
any other name.

Upstream never meets this, because its client is on the host and reaches a published port —
the Host header genuinely *is* `localhost:8000`. The jail is another container calling
`http://graphiti:8000/mcp`, which that allow-list rejects, and there is no config key,
environment variable or flag that extends it.

So `memory/mcp/jail_patches.py` wraps `FastMCP.__init__` to seed the allow-list from
`MCP_ALLOWED_HOSTS` — set to `graphiti:*` in `docker-compose.yml` — before the auto-enable
branch decides for itself. The protection stays **on**: it gains the names this compose file
publishes the server under, and nothing else. It is applied from `sitecustomize.py`, next to
the rebindings that point the server at Slater and at Bedrock.

### Seeding is a separate command, and `up` must never do it

Slater serves *compiled generations*: indexes are declared when a graph is built, not created at
runtime, and a label enters its symbol table only through an actual node. So the graph has to
exist, with its indexes and one seed row per label, before Graphiti connects —
`memory/schema/graphiti-schema.cypher` is that dump, generated from `graphiti-core` by
`gen_schema.py` beside it.

Running the seed against a graph that is already serving publishes a new generation underneath the
live server, and the write delta — bound to the generation it was written against — is abandoned.
Slater carries on accepting writes and never reads them back, so **the loss is silent**. That is
why `slater-seed` has its own profile and no `depends_on` anywhere: nothing can reach it by
accident, and an unseeded data directory fails loudly at boot instead.

The graph must be rebuilt when `graphiti-core`'s schema changes, when the entity types in
`memory/mcp/config.yaml` change, or when the embedder's dimension changes. On a graph already in
use that is not a re-seed: consolidate first (`CALL slater.consolidate()`), then rebuild from a
dump of the consolidated graph.

**Never delete the seed rows.** They carry `group_id: '_slater_seed_'`, which no Graphiti call
uses, so they are invisible to search — but a label leaves the symbol table with the last node
carrying it, and every later write carrying that label then fails.

### The password is a published one

`memory/acl/acl.json` grants one user, `graphiti`, read and write on one graph, and ships the
argon2id hash of `graphiti-example` — a throwaway, published in the upstream example on purpose so
it runs with no setup. Both services bind their host ports to `127.0.0.1`, which is what makes
that acceptable here and what would stop being true the moment either is bound to `0.0.0.0`.
`memory/acl/README.md` says how to mint your own; the graph carries the ACL's fingerprint, so
changing it means re-running `slater-seed`.

### Stopping it

```bash
docker compose --profile memory down        # stop
docker compose --profile memory down -v     # ...and delete the graph, the WAL and everything
                                            #    Claude ever remembered
```

## Rebuilding

After editing the `Dockerfile`, changing `UID`/`GID`, or to pick up a newer Claude Code release:

```bash
docker compose build --no-cache
```

Claude Code can also update itself in place: npm's global prefix is `/home/claude/.npm-global`,
inside the container user's own home, so the updater has somewhere it can write. That home is not
a mounted volume, though, so such an update lasts only as long as the container — rebuilding is
still what moves the version baked into the image forward.

## What's in the box

- `Dockerfile` — Ubuntu 26.04 + Node 22 + `@anthropic-ai/claude-code`, plus `git`, `jq`, `curl`,
  `less`, `openssh-client`, `gh`, and the Docker CLI (`docker-ce-cli`, buildx, compose plugins).
  Then the cloud CLIs: `aws` (AWS CLI v2), `az` (Azure CLI) and `pulumi`. `~/.aws`, `~/.azure` and
  `~/.pulumi` are all mounted from this repo, so an `aws configure`, `az login` or `pulumi login`
  outlives the container instead of having to be repeated every run. Creates a `claude` user matched
  to your host UID/GID so files written in the container are owned by you on the host.
- `docker-compose.yml` — the `claude` service: builds the image, wires up the volume mounts, joins
  the host's docker-socket group, and starts Claude Code in `/workspace`. Then `slater-seed`,
  `slater` and `graphiti`, all profile-gated and inert unless asked for; see [Memory](#memory).
- `.env.example` — the settings above, with comments.
- `.claude/` — Claude Code's state on the host: credentials, settings, and session history. Only
  `.gitkeep` is tracked; the rest is gitignored. `CLAUDE_CONFIG_DIR` points Claude Code here, which
  is what keeps `.claude.json` inside this directory instead of loose in `$HOME`.
- `.config/` — per-user CLI config inside the container: your git identity at `.config/git/config`,
  plus whatever else follows `XDG_CONFIG_HOME` (`gh`, for one). Tracked only as `.gitkeep`, in
  `.config/` and in `.config/git/` both; see [Git identity](#git-identity).
- `.ssh/` — `known_hosts`, written on first connection, and any ssh `config` for the container.
  Tracked only as `.gitkeep`; no keys go here, see [SSH agent](#ssh-agent).
- `.aws/` — the AWS CLI's config and credentials for the container, so `aws configure` (or an
  `aws sso login`) is done once rather than every run. Tracked only as `.gitkeep`; the rest is
  gitignored, because `.aws/credentials` holds a long-lived access key in plain text.
- `.azure/` — the Azure CLI's profile for the container, so `az login` survives a restart. Tracked
  only as `.gitkeep`; the rest is gitignored, as it caches refresh tokens.
- `.pulumi/` — Pulumi's state: the `pulumi login` token, and the provider plugins it downloads, so
  neither is fetched again next run. Tracked only as `.gitkeep`; the rest is gitignored, for the
  token and because `plugins/` is a cache that runs to hundreds of MB.
- `cc-jail/` — the default, empty `WORKSPACE_HOST`. Also tracked only as `.gitkeep`.
- `memory/` — the optional Graphiti-on-Slater memory stack: the seed schema and its generator,
  Slater's ACL, and the Dockerfile, config and startup shims for Graphiti's MCP server. All of
  it comes from [graphiti-slater](https://github.com/Hikari-Systems/graphiti-slater)'s own
  runnable example, bar `mcp/jail_patches.py`, which is cc-jail's — see
  [Why the server needed one patch](#why-the-server-needed-one-patch). The adapter itself is
  not vendored: the Dockerfile installs it from a commit-pinned source archive. Inert unless
  the `memory` profile is used; see [Memory](#memory).

## Notes

- The mounted directories are all tracked as `.gitkeep` on purpose. Docker creates a missing bind
  source as a `root`-owned directory, which the unprivileged `claude` user then can't write to —
  shipping them in the repo means they exist, owned by whoever cloned. `.config/git/` is kept for
  the plainer reason that a directory you have to `mkdir` first is a step in the setup, and this
  one no longer is.
- Global npm packages install to `/home/claude/.npm-global` (`NPM_CONFIG_PREFIX`), not to npm's
  default prefix of `/usr`. Anywhere `root` owns leaves Claude Code unable to update itself, which
  it reports as "npm global folder isn't writable" / "No write permissions for auto-updates". The
  shims are symlinked into `/usr/local/bin` too, so `claude`, `lin` and `cup` still resolve when a
  forwarded host `PATH` replaces the image's own.
- `HOME` is pinned to `/home/claude` in `docker-compose.yml`. Forwarding the host environment
  (`--env-from-file <(env)`, or a bare `-e HOME`) otherwise carries the host's `HOME` in, and every
  tool that resolves a dotfile through it — git, ssh, `gh`, npm — then reads from a directory that
  does not exist in the container. `XDG_RUNTIME_DIR` is pinned for the same reason.
- `sudo` is installed and `claude` is in the `sudo` group, but the account's password is locked
  (`passwd -S claude` → `L`), so `sudo` prompts for a password that doesn't exist and fails. To use
  it for ad-hoc `apt-get install` during a session, add a rule in the `Dockerfile`:
  `RUN echo 'claude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/claude`. Anything you want permanently
  installed belongs in the `Dockerfile` regardless.
- Container state is disposable. Everything that matters — your code, your Claude config — is on
  the host via the mounts.
