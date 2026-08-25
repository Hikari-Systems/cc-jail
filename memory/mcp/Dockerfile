# The stock Graphiti MCP server, taught to speak to Slater and to reach Claude through
# AWS Bedrock.
#
# `-standalone` matters: the plain `zepai/knowledge-graph-mcp` tag is the *combined*
# image (FROM falkordb/falkordb, entrypoint starts redis-server), which would run a
# second graph database beside the one we want. There is no `latest-standalone`; the
# standalone tags are `standalone`, `1.0.2-standalone`, and the fully explicit
# `1.0.2-graphiti-0.28.2-standalone`.
ARG GRAPHITI_MCP_TAG=1.0.2-standalone
FROM zepai/knowledge-graph-mcp:${GRAPHITI_MCP_TAG}

# ── Everything installs into the server's own venv ────────────────────────────
#
# The image runs `uv run --no-sync main.py` from /app/mcp, against /app/mcp/.venv. The
# system interpreter is a different environment entirely — installing there looks like it
# worked, imports fine at build time, and leaves the server running the versions it
# shipped with. `--no-sync` means uv will not repair it either.
#
# So every install below is pinned to that interpreter, and the assertion at the end runs
# under it too.
ENV MCP_VENV=/app/mcp/.venv/bin/python
ENV PATH=/root/.local/bin:$PATH

# ── The graphiti-core version skew, resolved deliberately ─────────────────────
#
# The published server image pins **graphiti-core 0.28.2**. This example is built and
# verified against **0.29.3**: Slater's write compatibility was proven against 0.29.3's
# node and edge save statements (which differ from 0.28.2's), and the adapter delegates
# its fulltext query builder to `graphiti_core/driver/falkordb/fulltext.py`, which 0.28.2
# does not have. The seed dump is generated from 0.29.3 too, though there the two happen
# to agree — the index schema is identical.
#
# The exact pin is the version everything was verified against; it is not a floor,
# because a minor bump should arrive by someone changing this line and re-running
# `scripts/verify.py`, not on its own. The server's own code was written against 0.28.2,
# so this pin is the first thing to suspect if the MCP layer misbehaves in a way Slater
# and the adapter do not explain.
ARG GRAPHITI_CORE_VERSION=0.29.3
RUN uv pip install --python ${MCP_VENV} --no-cache "graphiti-core==${GRAPHITI_CORE_VERSION}"

# The Anthropic SDK, which the stock image does not ship at all — so `provider:
# anthropic` fails in it today whether you reach Claude directly or through Bedrock
# (`HAS_ANTHROPIC` is an import guard around exactly this). The `[bedrock]` extra adds
# the boto3/botocore that sign AWS requests; the Messages API surface is the same one the
# direct client uses.
RUN uv pip install --python ${MCP_VENV} --no-cache "anthropic[bedrock]"

# The Voyage SDK, absent for the same reason the Anthropic one is: the standalone image
# ships only the OpenAI client, so `provider: voyage` silently falls back to
# `OpenAIEmbedder()` and dies asking for an OPENAI_API_KEY. `HAS_VOYAGE_EMBEDDER` is the
# import guard that decides it.
RUN uv pip install --python ${MCP_VENV} --no-cache voyageai

# The Slater graph adapter. cc-jail has no checkout of it to copy from — the upstream
# example installs from the source beside it — so it comes from a GitHub source archive
# pinned to a **commit**, not a branch: the adapter and the seed dump in ../schema were
# generated from the same graphiti-core, and a moving ref would let them drift apart
# behind an unchanged Dockerfile.
#
# A tarball URL rather than `git+https://…` because the stock server image is not
# guaranteed to carry a git binary, and this needs none. It has to be the `/archive/…`
# spelling, whose path ends in `.tar.gz`: uv reads the *extension* to decide what it has
# been handed, and refuses the otherwise-equivalent `codeload.github.com/…/tar.gz/<sha>`
# for ending in a commit id instead.
#
# `--no-deps` so the adapter's own dependency floor cannot quietly re-resolve the
# graphiti-core pinned above.
ARG GRAPHITI_SLATER_REF=0ca70a5cf610f115e27359edb27d79d9dc39129a
RUN uv pip install --python ${MCP_VENV} --no-cache --no-deps \
    "https://github.com/Hikari-Systems/graphiti-slater/archive/${GRAPHITI_SLATER_REF}.tar.gz"

# Imported at interpreter startup, before the server constructs anything — so every
# rebinding is in place before graphiti_core is first imported.
COPY bedrock_patches /app/mcp/bedrock_patches
COPY jail_patches.py /app/mcp/jail_patches.py
COPY sitecustomize.py /app/mcp/sitecustomize.py
ENV PYTHONPATH=/app/mcp

# Fail the build, not the deployment, if any of that landed in the wrong environment.
RUN ${MCP_VENV} -c "\
from importlib.metadata import version; \
import graphiti_slater, bedrock_patches, jail_patches; \
from anthropic import AsyncAnthropicBedrock; \
from graphiti_core.llm_client.anthropic_client import AnthropicClient; \
from graphiti_core.embedder.voyage import VoyageAIEmbedder; \
from bedrock_patches.reranker import BedrockRerankerClient; \
from graphiti_core.driver.falkordb.fulltext import build_falkor_fulltext_query; \
v = version('graphiti-core'); \
assert v == '${GRAPHITI_CORE_VERSION}', f'graphiti-core is {v}, not ${GRAPHITI_CORE_VERSION}'; \
print('ok: graphiti-core', v, 'in', __import__('sys').prefix)"
