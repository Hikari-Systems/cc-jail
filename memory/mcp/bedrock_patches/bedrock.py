"""Reach Claude through AWS Bedrock instead of the Anthropic API directly.

Graphiti's `AnthropicClient` needs almost nothing to make this work, because the
Anthropic SDK ships a Bedrock client with the same Messages API: `AsyncAnthropicBedrock`
answers `messages.create(system=…, messages=…, model=…, tools=…, tool_choice=…)`, which
is the one call `AnthropicClient` makes. And that class already accepts an injected
`client=`, so nothing about its request building, tool-based structured output, retries
or error mapping has to change.

What does change is where the credentials come from — AWS SigV4 over the ambient
credential chain, not an `ANTHROPIC_API_KEY` — and the model identifier.

The MCP server's factory offers no hook for either (it constructs
`AnthropicClient(config=llm_config)` by name, having imported the class into its own
namespace), so this rebinds the class at its source. `sitecustomize` runs before any
application import, so the factory's later `from … import AnthropicClient` picks this up.
Same trick, and same reason, as the Slater driver rebinding beside it.
"""

from __future__ import annotations

import logging
import os
import typing

logger = logging.getLogger(__name__)

#: Where the Bedrock endpoint lives. Model availability and quota are both per-region,
#: and a cross-region inference profile ID (`us.anthropic.…`) must match the region's
#: geography — a `us.` profile does not resolve from `eu-west-1`.
DEFAULT_REGION = 'us-east-1'


def _bedrock_client() -> typing.Any:
    """An `AsyncAnthropicBedrock` on the ambient AWS credential chain.

    No credentials are read or forwarded here on purpose: botocore resolves them the
    way every other AWS client in the estate does — environment, shared config, or the
    container/instance role — so a task role works with nothing set, and nothing
    long-lived has to be handed to the container.
    """
    from anthropic import AsyncAnthropicBedrock

    return AsyncAnthropicBedrock(
        aws_region=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or DEFAULT_REGION,
        # One retry, matching what graphiti's own construction of the direct client
        # asks for: the caller above it already retries at the episode level, and a
        # deeper retry mostly multiplies the bill for a request that will fail again.
        max_retries=1,
    )


def install() -> None:
    """Rebind `graphiti_core`'s `AnthropicClient` to a Bedrock-backed subclass.

    Idempotent. Safe to call before `graphiti_core` is imported — this triggers the
    import itself.
    """
    from graphiti_core.llm_client import anthropic_client as mod

    if getattr(mod.AnthropicClient, '_hs_bedrock', False):
        return

    base = mod.AnthropicClient

    class BedrockAnthropicClient(base):  # type: ignore[valid-type,misc]
        """`AnthropicClient` talking to Bedrock.

        Only the transport differs. Everything above it — the tool-based structured
        output, the JSON salvage path, token accounting, rate-limit and refusal mapping
        — is inherited unchanged, which is the point: this is a routing decision, not a
        second implementation to keep in step.
        """

        _hs_bedrock = True

        def __init__(self, config=None, cache: bool = False, client=None, **kwargs):
            # `config.api_key` is ignored: Bedrock authenticates with AWS credentials.
            # The MCP factory validates that the key is non-empty before it gets here,
            # so config.yaml carries a placeholder — see the comment there.
            super().__init__(config=config, cache=cache, client=client or _bedrock_client(), **kwargs)
            logger.info(
                'LLM calls routed through AWS Bedrock (region=%s, model=%s)',
                os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or DEFAULT_REGION,
                getattr(self, 'model', '?'),
            )

    mod.AnthropicClient = BedrockAnthropicClient  # type: ignore[misc]
    logger.info('graphiti-slater example: Anthropic calls will go through AWS Bedrock')
