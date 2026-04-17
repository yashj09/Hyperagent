"""
AI wrapper that adds Claude Haiku reasoning via AWS Bedrock to any strategy.

Wraps an existing BaseStrategy and, when a signal exceeds the confidence
threshold, calls Claude Haiku to generate a 2-3 sentence explanation of the
trade rationale. The reasoning is attached to signal.ai_reasoning.
"""

import asyncio
import logging
from typing import Optional, Dict

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class AIWrapper(BaseStrategy):
    """Wraps any BaseStrategy and enriches signals with AI reasoning."""

    def __init__(self, strategy: BaseStrategy):
        self.strategy = strategy
        self._client = None

    @property
    def name(self) -> str:
        return f"{self.strategy.name} + AI"

    @property
    def description(self) -> str:
        return f"{self.strategy.description} (with Claude Haiku reasoning)"

    def _get_client(self):
        """Lazy-init the Anthropic Bedrock client."""
        if self._client is None:
            import anthropic

            self._client = anthropic.AnthropicBedrock(aws_region=config.AWS_REGION)
        return self._client

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        """Generate signal from wrapped strategy, then add AI reasoning if strong enough."""
        signal = await self.strategy.generate_signal(state)

        if signal and signal.score >= config.CASCADE_SIGNAL_THRESHOLD:
            try:
                reasoning = await asyncio.to_thread(
                    self._get_reasoning, signal, state
                )
                signal.ai_reasoning = reasoning
            except Exception as e:
                logger.warning(f"AI reasoning failed: {e}")
                signal.ai_reasoning = f"AI unavailable: {e}"

        return signal

    def _get_reasoning(self, signal: Signal, state: AgentState) -> str:
        """Synchronous call to Claude Haiku for signal reasoning."""
        client = self._get_client()
        prompt = self._build_prompt(signal, state)

        response = client.messages.create(
            model=config.AI_MODEL_ID,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _build_prompt(self, signal: Signal, state: AgentState) -> str:
        """Build a concise prompt asking Haiku to explain the signal."""
        current_price = state.prices.get(signal.coin, 0)
        funding = state.funding_rates.get(signal.coin, 0)
        oi = state.open_interest.get(signal.coin, 0)

        return (
            f"You are a crypto trading analyst. In 2-3 sentences, explain why this "
            f"trade signal makes sense and what risks to watch.\n\n"
            f"Asset: {signal.coin}\n"
            f"Direction: {signal.direction}\n"
            f"Strategy: {signal.strategy}\n"
            f"Score: {signal.score:.1f}/100 ({signal.confidence})\n"
            f"Reason: {signal.reason}\n"
            f"Current price: ${current_price:,.2f}\n"
            f"Funding rate: {funding:.6f}\n"
            f"Open interest: ${oi:,.0f}\n\n"
            f"Be specific and concise. Focus on the key risk/reward."
        )

    def get_config_schema(self) -> Dict:
        """Merge wrapped strategy config with AI config."""
        schema = self.strategy.get_config_schema()
        schema["ai_enabled"] = {
            "type": "bool",
            "default": config.AI_ENABLED_DEFAULT,
            "description": "Enable AI reasoning for signals",
        }
        schema["ai_model"] = {
            "type": "str",
            "default": config.AI_MODEL_ID,
            "description": "Bedrock model ID for reasoning",
        }
        return schema

    async def initialize(self):
        """Initialize the wrapped strategy."""
        await self.strategy.initialize()

    async def cleanup(self):
        """Clean up the wrapped strategy."""
        await self.strategy.cleanup()
