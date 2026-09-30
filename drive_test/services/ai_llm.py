"""Optional LLM narrative for the AI Analyst.

Disabled by default. When DRIVE_TEST_AI_LLM_ENABLED is true and the Anthropic
SDK + an API key are available, this turns the deterministic evidence into a
short narrative. The model is given ONLY the pre-computed evidence and is
instructed never to introduce numbers of its own — it phrases, it does not
measure. Any failure degrades silently to the deterministic output.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings

logger = logging.getLogger('drive_test')

_SYSTEM = (
    "You are a telecom RF drive-test analyst. You will be given a JSON block of "
    "MEASURED FACTS, OBSERVED PATTERNS, POSSIBLE CAUSES and RECOMMENDATIONS that "
    "were computed from a database. Write a concise professional narrative (max "
    "180 words) that explains the findings to a network engineer. Absolute rules: "
    "use ONLY the numbers present in the evidence; never invent or estimate a "
    "value; keep measured facts, observed patterns and possible causes clearly "
    "distinct; describe causes as hypotheses to investigate, not conclusions."
)


def llm_enabled() -> bool:
    return bool(getattr(settings, 'DRIVE_TEST_AI_LLM_ENABLED', False))


def narrate(evidence: dict) -> str | None:
    """Return an LLM narrative, or None when disabled/unavailable/failed."""
    if not llm_enabled():
        return None
    try:
        import anthropic  # optional dependency
    except ImportError:
        logger.info('AI narrative requested but anthropic SDK is not installed.')
        return None

    try:
        client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY / profile
        model = getattr(settings, 'DRIVE_TEST_AI_MODEL', 'claude-opus-5-5')
        question = evidence.get('question') or 'Summarise the network performance on this route.'
        payload = {k: evidence[k] for k in ('facts', 'patterns', 'causes', 'recommendations')}
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            thinking={'type': 'adaptive'},
            system=_SYSTEM,
            messages=[{
                'role': 'user',
                'content': f'Question: {question}\n\nEVIDENCE:\n{json.dumps(payload, default=str)}',
            }],
        )
        return ''.join(b.text for b in response.content if b.type == 'text').strip() or None
    except Exception:
        logger.exception('AI narrative generation failed; falling back to deterministic output.')
        return None
