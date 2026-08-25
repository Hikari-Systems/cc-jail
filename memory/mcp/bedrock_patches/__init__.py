"""The two rebindings this example needs, applied before anything imports Graphiti.

All three exist for the same reason: the MCP server and graphiti-core pick their driver,
LLM client and cross-encoder by name, from factories that hard-match a closed set of
strings — and in the cross-encoder's case from no factory at all. None offers a hook. So
rather than fork the server, the names are rebound at their source before the server
runs; `sitecustomize` is imported at interpreter startup, which is early enough.

* `graphiti_slater.install()` — the graph goes to Slater over Bolt.
* `bedrock.install()`         — Claude is reached through AWS Bedrock, not the
                                Anthropic API directly.
* `reranker.install()`        — and so is reranking, which otherwise falls back to
                                OpenAI whatever the config says.
"""

from bedrock_patches import bedrock, reranker


def install() -> None:
    """Apply every rebinding. Idempotent."""
    import graphiti_slater

    graphiti_slater.install()
    bedrock.install()
    reranker.install()


__all__ = ['bedrock', 'install', 'reranker']
