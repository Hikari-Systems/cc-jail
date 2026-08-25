# hs-claude

A containerised [Claude Code](https://claude.com/claude-code) environment. Claude Code runs inside
an Ubuntu container as an unprivileged `claude` user, with your source tree bind-mounted in — so
the agent sees only the directories you explicitly mount, and its credentials and config live in
this repo rather than on your host.

The container also gets a Docker CLI wired to the **host's** daemon, so Claude can build and run
containers as part of its work. See [Docker access](#docker-access) — this is convenient, and it
is also the one thing that punches a hole through the isolation above.

## Quick start

```bash
git clone <repo-url> hs-claude && cd hs-claude
docker compose run --rm claude
```

If the host's docker socket group doesn't happen to match the image's, use this instead — it reads
the GID off the socket and passes it in ([why](#docker-socket-group)):

```bash
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%g' /var/run/docker.sock) \
  docker compose run --rm claude
```

Either way you get a bash shell in `/workspace` with `claude` on the `PATH`. The first `claude`
invocation walks you through logging in; credentials are written to `.claude/` in this repo, so
later runs start straight up.

Nothing else needs setting up — the mount points ship with the repo, and `--rm` means each session
leaves no stopped container behind.

By default `/workspace` is `./cc-jail`, an empty directory in this repo, which is a deliberately
useless place to work. Point it at your actual code with `WORKSPACE_HOST`:

```bash
WORKSPACE_HOST=~/git/hs docker compose run --rm claude
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

### Where your code is mounted

`WORKSPACE_HOST` is the one that matters. Anything **not** mounted is invisible to the agent —
that's the point: mount the projects you're working on, and nothing else.

`WORKSPACE_PATH` decides where that tree appears inside the container. Mounting it at the *same*
path on both sides is worth doing if Claude will run `docker` itself — see
[Docker access](#docker-access) for why:

```dotenv
WORKSPACE_HOST=/home/you/git/hs
WORKSPACE_PATH=/home/you/git/hs
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
hsclaude() {
  ( cd ~/git/hs/hs-claude \
    && DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) \
       docker compose run --rm claude "${@:-bash}" )
}
```

`hsclaude` for a shell, `hsclaude claude` to go straight into the agent. The subshell keeps the `cd`
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

## Rebuilding

After editing the `Dockerfile`, changing `UID`/`GID`, or to pick up a newer Claude Code release:

```bash
docker compose build --no-cache
```

## What's in the box

- `Dockerfile` — Ubuntu 26.04 + Node 22 + `@anthropic-ai/claude-code`, plus `git`, `jq`, `curl`,
  `less`, `openssh-client`, and the Docker CLI (`docker-ce-cli`, buildx, compose plugins). Creates
  a `claude` user matched to your host UID/GID so files written in the container are owned by you
  on the host.
- `docker-compose.yml` — the `claude` service: builds the image, wires up the volume mounts, joins
  the host's docker-socket group, and drops you at a shell in `/workspace`.
- `.env.example` — the settings above, with comments.
- `.claude/` — Claude Code's state on the host: credentials, settings, and session history. Only
  `.gitkeep` is tracked; the rest is gitignored. `CLAUDE_CONFIG_DIR` points Claude Code here, which
  is what keeps `.claude.json` inside this directory instead of loose in `$HOME`.
- `cc-jail/` — the default, empty `WORKSPACE_HOST`. Also tracked only as `.gitkeep`.

## Notes

- Both mounted directories are tracked as `.gitkeep` on purpose. Docker creates a missing bind
  source as a `root`-owned directory, which the unprivileged `claude` user then can't write to —
  shipping them in the repo means they exist, owned by whoever cloned.
- `sudo` is installed and `claude` is in the `sudo` group, but the account's password is locked
  (`passwd -S claude` → `L`), so `sudo` prompts for a password that doesn't exist and fails. To use
  it for ad-hoc `apt-get install` during a session, add a rule in the `Dockerfile`:
  `RUN echo 'claude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/claude`. Anything you want permanently
  installed belongs in the `Dockerfile` regardless.
- Container state is disposable. Everything that matters — your code, your Claude config — is on
  the host via the mounts.
