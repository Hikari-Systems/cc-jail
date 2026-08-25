"""A cross-encoder that reranks with Claude on Bedrock.

Graphiti always builds a cross-encoder — `Graphiti.__init__` falls back to
`OpenAIRerankerClient()` when none is passed — and the MCP server never passes one. So
with any LLM other than OpenAI the server dies at startup asking for an
`OPENAI_API_KEY`, whatever the config says. That is true of Anthropic-direct too; it is
not something reaching Claude through Bedrock introduced.

Of the three clients graphiti ships, the OpenAI one needs token logprobs (Claude has
none), the Gemini one needs a Google key, and the BGE one downloads ~2.3 GB of model
weights and reranks on the CPU. This is the Gemini client's approach — score each
passage 0–100 in its own tiny completion, normalise, sort — pointed at Bedrock, so the
vendor surface stays exactly what was asked for: Bedrock and Voyage.

**Cost shape, worth knowing before enabling a big search recipe:** ranking is one model
call per passage, so a single search with a limit of 20 is 20 calls. That is graphiti's
own design — the Gemini client it is modelled on does the same — and it is the reason
`BEDROCK_RERANKER_MODEL` exists: point it at the cheapest id the account can invoke.
"""

from __future__ import annotations

import logging
import os
import re

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.helpers import semaphore_gather
from graphiti_core.llm_client import LLMConfig, RateLimitError

logger = logging.getLogger(__name__)

#: Reranking is one model call per passage, so the cheapest capable model wins — but
#: which ids are callable is per account and region. This default is a **local**
#: (region-native, on-demand) id, verified invokable in the deployment this stack was
#: built for; there is no on-demand Haiku there, so it is the same class of model as
#: the extractor rather than a cheaper one. If a cheaper id is available, set
#: BEDROCK_RERANKER_MODEL — that is the knob, and it is worth using.
DEFAULT_RERANK_MODEL = 'anthropic.claude-sonnet-4-6'

SYSTEM = (
    'You are an expert at rating passage relevance. '
    'Respond with only a number from 0-100.'
)


class BedrockRerankerClient(CrossEncoderClient):
    """Rank passages by relevance to a query, scoring each with Claude on Bedrock."""

    def __init__(self, config: LLMConfig | None = None, client=None):
        self.config = config or LLMConfig()
        if client is None:
            from bedrock_patches.bedrock import _bedrock_client

            client = _bedrock_client()
        self.client = client
        self.model = os.getenv('BEDROCK_RERANKER_MODEL') or DEFAULT_RERANK_MODEL

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        """Score each passage 0–100, normalise to [0, 1], and sort descending.

        A passage whose score cannot be read is kept at 0.0 rather than dropped: the
        caller is choosing an order, and silently losing a candidate is worse than
        ranking it last.
        """
        if len(passages) <= 1:
            return [(passage, 1.0) for passage in passages]

        async def score(passage: str) -> float:
            result = await self.client.messages.create(
                model=self.model,
                system=SYSTEM,
                max_tokens=8,
                temperature=0.0,
                messages=[
                    {
                        'role': 'user',
                        'content': (
                            'Rate how well this passage answers or relates to the '
                            'query. Use a scale from 0 to 100.\n\n'
                            f'Query: {query}\n\n'
                            f'Passage: {passage}\n\n'
                            'Provide only a number between 0 and 100 '
                            '(no explanation, just the number):'
                        ),
                    }
                ],
            )
            text = ''.join(
                block.text for block in result.content if getattr(block, 'type', None) == 'text'
            ).strip()
            match = re.search(r'\b(\d{1,3})\b', text)
            if not match:
                logger.warning('could not read a score from the reranker response: %r', text)
                return 0.0
            return max(0.0, min(1.0, float(match.group(1)) / 100.0))

        try:
            scores = await semaphore_gather(*[score(p) for p in passages])
        except Exception as e:
            # The rate-limit type graphiti's retry logic knows about, raised by the
            # same SDK the direct client uses — Bedrock throttling arrives as a 429.
            if type(e).__name__ == 'RateLimitError':
                raise RateLimitError from e
            raise

        ranked = list(zip(passages, scores, strict=True))
        ranked.sort(reverse=True, key=lambda pair: pair[1])
        return ranked


def install() -> None:
    """Make Graphiti's default cross-encoder this one.

    `Graphiti.__init__` constructs `OpenAIRerankerClient()` by name from its own module
    namespace, and the MCP server offers no way to pass an alternative — so the name is
    rebound at its source, before anything imports it.
    """
    import graphiti_core.graphiti as graphiti_module

    if getattr(graphiti_module, 'OpenAIRerankerClient', None) is BedrockRerankerClient:
        return
    graphiti_module.OpenAIRerankerClient = BedrockRerankerClient  # type: ignore[attr-defined]
    logger.info('graphiti-slater example: reranking will use Claude on AWS Bedrock')
