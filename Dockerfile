FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

ARG CLAUDE_UID=1000
ARG CLAUDE_GID=1000
# GID of the group that owns /var/run/docker.sock on the HOST.
# Find it with:  stat -c '%g' /var/run/docker.sock
#
# This only gives that GID a name inside the image; the socket is a runtime
# object, so docker-compose.yml also passes the GID as a supplementary group
# (group_add). That runtime path is the one that grants access -- a container
# started from an image built with a stale value still works.
ARG DOCKER_GID=999

# Claude Code updates itself in place, so the directory it lives in has to be
# writable by the user that runs it. npm's default global prefix here is /usr,
# which root owns -- so every update attempt fails ("npm global folder isn't
# writable", "No write permissions for auto-updates") and the version baked
# into the image is the version you are stuck on. Point npm's global prefix at
# the container user's own home instead: every `npm install --global` below
# lands there, and the updater can write to it at run time.
ENV NPM_CONFIG_PREFIX=/home/claude/.npm-global
ENV PATH=/home/claude/.npm-global/bin:$PATH

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		bash \
		ca-certificates \
		curl \
		git \
		gnupg \
		jq \
		less \
		openssh-client \
		procps \
		sudo \
		unzip \
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& install -m 0755 -d /etc/apt/keyrings \
	&& curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
	&& chmod a+r /etc/apt/keyrings/docker.asc \
	# download.docker.com lags new Ubuntu releases; fall back to the latest LTS
	# suite. Probe the package index rather than the suite's Release file: these
	# repos publish a signed but EMPTY suite for a new Ubuntu release long before
	# they put a package in it, so a Release file is not evidence that anything
	# installable is behind it (see the azure-cli note below, where exactly that
	# happened). Asking for the package by name can't be fooled either way.
	&& DOCKER_SUITE="$(. /etc/os-release && echo "$VERSION_CODENAME")" \
	&& if ! curl -fsSL "https://download.docker.com/linux/ubuntu/dists/${DOCKER_SUITE}/stable/binary-$(dpkg --print-architecture)/Packages" 2>/dev/null \
		| grep -q '^Package: docker-ce-cli$'; then \
		DOCKER_SUITE=noble; \
	fi \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${DOCKER_SUITE} stable" \
		> /etc/apt/sources.list.d/docker.list \
	&& apt-get update \
	# CLI + plugins only: this container talks to the host's daemon over the
	# mounted socket, so the docker-ce engine package would never be used.
	&& apt-get install -y --no-install-recommends \
		docker-ce-cli \
		docker-buildx-plugin \
		docker-compose-plugin \
	&& if getent passwd "${CLAUDE_UID}" >/dev/null; then userdel "$(getent passwd "${CLAUDE_UID}" | cut -d: -f1)"; fi \
	&& if ! getent group "${CLAUDE_GID}" >/dev/null; then groupadd --gid "${CLAUDE_GID}" claude; fi \
	&& useradd --uid "${CLAUDE_UID}" --gid "${CLAUDE_GID}" --create-home --shell /bin/bash claude \
	&& usermod --append --groups sudo claude \
	# Add claude to the group owning /var/run/docker.sock on the host, so the
	# mounted socket is writable without sudo. Reuse whatever group already
	# holds that GID (e.g. root=0 under Docker Desktop); only create one if the
	# GID is free, falling back on a different name if "docker" is taken.
	&& if ! getent group "${DOCKER_GID}" >/dev/null; then \
		groupadd --gid "${DOCKER_GID}" docker \
		|| groupadd --gid "${DOCKER_GID}" dockerhost; \
	fi \
	&& DOCKER_GROUP="$(getent group "${DOCKER_GID}" | cut -d: -f1)" \
	&& usermod --append --groups "${DOCKER_GROUP}" claude \
	# Sanity check: the group logic above must have taken effect.
	&& id -nG claude | tr ' ' '\n' | grep -qx "${DOCKER_GROUP}" \
	#
    # ADD BELOW HERE ANYTHING ELSE YOU WANT TO INSTALL IN THE IMAGE, e.g.:
    # && apt-get install -y --no-install-recommends <package> \
	#
	# GitHub CLI, from GitHub's own apt repo. Its "stable" suite is
	# distro-independent, so this needs no codename fallback like docker's.
	#
	&& curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
		-o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
		> /etc/apt/sources.list.d/github-cli.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends gh \
	#
	# ── Cloud and editor CLIs ────────────────────────────────────────────
	#
	# Two architectures to name, and AWS and Pulumi spell them differently, so
	# the mapping is done once here: dpkg's own names on the left, and on the
	# right what each of those two calls the same machines.
	&& case "$(dpkg --print-architecture)" in \
		amd64) AWS_ARCH=x86_64; ALT_ARCH=x64 ;; \
		arm64) AWS_ARCH=aarch64; ALT_ARCH=arm64 ;; \
		*) echo "unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
	esac \
	# AWS CLI v2, which is not in apt at all -- Amazon ships it only as this
	# bundle. --bin-dir and --install-dir are spelled out because the default
	# for the second one is /usr/local/aws-cli anyway but the first defaults
	# relative to the extracted directory, which is about to be deleted.
	&& curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip \
	&& unzip -q /tmp/awscliv2.zip -d /tmp \
	&& /tmp/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli \
	&& rm -rf /tmp/awscliv2.zip /tmp/aws \
	# Azure CLI, from Microsoft's apt repo. Same shape as docker's above and
	# for the same reason: packages.microsoft.com lags new Ubuntu releases, so
	# fall back to the latest LTS suite when this one has no azure-cli in it.
	#
	# "Has a Release file" is not the same question. Microsoft publishes the new
	# suite -- signed, listed, and completely empty -- as soon as the Ubuntu
	# release exists: 26.04 "resolute" has a valid Release whose Packages index
	# is zero bytes. apt indexes that without complaint and then the install
	# fails with "E: Unable to locate package azure-cli". So probe for the
	# package, which is what we actually need, instead of the suite.
	&& curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
		-o /etc/apt/keyrings/microsoft.asc \
	&& chmod a+r /etc/apt/keyrings/microsoft.asc \
	&& AZURE_SUITE="$(. /etc/os-release && echo "$VERSION_CODENAME")" \
	&& if ! curl -fsSL "https://packages.microsoft.com/repos/azure-cli/dists/${AZURE_SUITE}/main/binary-$(dpkg --print-architecture)/Packages" 2>/dev/null \
		| grep -q '^Package: azure-cli$'; then \
		AZURE_SUITE=noble; \
	fi \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/microsoft.asc] https://packages.microsoft.com/repos/azure-cli ${AZURE_SUITE} main" \
		> /etc/apt/sources.list.d/azure-cli.list \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends azure-cli \
	# Pulumi, as the tarball its own installer script would fetch -- skipping
	# the script because it installs into $HOME/.pulumi/bin, which is neither
	# on PATH nor owned by root at this point in the build. The tarball holds
	# the pulumi binary plus its per-language plugin helpers, and they all have
	# to travel together, so the whole directory goes to /usr/local/bin.
	#
	# Unpinned, like every other version in this image: it takes whatever is
	# current when you build. Pin it by replacing the lookup with a literal.
	&& PULUMI_VERSION="$(curl -fsSL https://www.pulumi.com/latest-version)" \
	&& curl -fsSL "https://get.pulumi.com/releases/sdk/pulumi-v${PULUMI_VERSION}-linux-${ALT_ARCH}.tar.gz" \
		| tar -xz -C /tmp \
	&& mv /tmp/pulumi/* /usr/local/bin/ \
	&& rmdir /tmp/pulumi \
	# Every --global install happens here, after useradd, so npm's prefix (set
	# above) lands in a /home/claude that already exists and belongs to the
	# container user rather than to root. Alongside Claude Code: Linear's own
	# CLI (command: "lin") and a ClickUp CLI (command: "cup"). ClickUp
	# publishes no official CLI, so this is the most widely used third-party
	# one; swap the package if you prefer another.
	&& npm install --global \
		@anthropic-ai/claude-code \
		@linear/cli \
		@krodak/clickup-cli \
	# That prefix is only on PATH through this image's ENV, and forwarding the
	# host environment into the container (see the README) replaces PATH along
	# with everything else. Symlink the shims into a directory that is on every
	# PATH, so `claude`, `lin` and `cup` resolve either way.
	&& for shim in /home/claude/.npm-global/bin/*; do \
		ln -sf "$shim" "/usr/local/bin/$(basename "$shim")"; \
	done \
	# npm ran as root, so the tree it just wrote is root's. Hand it back.
	&& chown -R "${CLAUDE_UID}:${CLAUDE_GID}" /home/claude \
	&& chmod 755 /home/claude \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
USER claude

CMD ["claude"]
