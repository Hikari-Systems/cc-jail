"""Rebind what Graphiti constructs by name, before any application code imports it.

Python imports `sitecustomize` automatically at interpreter startup, which is the only
hook available: the MCP server picks its graph driver and its LLM client from factories
that hard-match a closed set of provider strings, and then construct the classes by
name. So the stock image is used unmodified, and this makes it build a `SlaterDriver`
and route Claude through AWS Bedrock.

See bedrock_patches/ for what each rebinding does and why.
"""

from bedrock_patches import install

install()

# cc-jail's own addition, not part of the upstream example: the client here is another
# container on the compose network rather than something on the host, so the server has to
# accept a Host header that is not localhost. See jail_patches.py.
import jail_patches

jail_patches.install()
