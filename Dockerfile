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
	&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
	&& apt-get install -y --no-install-recommends nodejs \
	&& npm install --global @anthropic-ai/claude-code \
	&& install -m 0755 -d /etc/apt/keyrings \
	&& curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
	&& chmod a+r /etc/apt/keyrings/docker.asc \
	# download.docker.com lags new Ubuntu releases; fall back to the latest LTS suite.
	&& DOCKER_SUITE="$(. /etc/os-release && echo "$VERSION_CODENAME")" \
	&& if ! curl -fsIL "https://download.docker.com/linux/ubuntu/dists/${DOCKER_SUITE}/Release" >/dev/null 2>&1; then \
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
    # ADD HERE ANYTHING ELSE YOU WANT TO INSTALL IN THE IMAGE, e.g.:
    # && apt-get install -y --no-install-recommends <package> \
	&& chmod 755 /home/claude \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
USER claude

CMD ["claude"]
