"""Configuration from environment variables (prefix ``AGORA_``) or a .env file."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGORA_", env_file=".env", extra="ignore")

    # LLM: any OpenAI-compatible endpoint with tool calling. "policy" = offline rule-based traders.
    brain: Literal["llm", "policy"] = "policy"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    researcher_model: str | None = None  # cheaper model for the researcher sub-agent

    # floor
    db_path: Path = Path(".agora/floor.db")
    seed: int = 7
    starting_cash: float = 100_000.0
    max_agent_turns: int = 10
    transport: Literal["memory", "stdio"] = "memory"  # stdio = each MCP server in its own process

    # risk policy (enforced inside the accounts MCP server, not by the agent)
    max_position_pct: float = 0.30
    max_order_pct: float = 0.25
    max_trades_per_cycle: int = 6
    commission_bps: float = 5.0
    spread_bps: float = 10.0
    min_rationale_chars: int = 15


def get_settings() -> Settings:
    return Settings()
