"""Read and price the content-free model usage ledger."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal

from wingman.domain.model_usage import ModelUsage
from wingman.infrastructure.config import Config


@dataclass(frozen=True)
class PricedUsage:
    usage: ModelUsage
    cost_usd: Decimal | None


def price_usage(config: Config, rows: list[ModelUsage]) -> list[PricedUsage]:
    """Apply current workspace prices without changing the immutable raw units."""
    try:
        models = tomllib.loads(config.models_config_path.read_text(encoding="utf-8")).get(
            "models", {}
        )
    except (OSError, tomllib.TOMLDecodeError):
        models = {}
    result: list[PricedUsage] = []
    for row in rows:
        entry = models.get(row.capability, {}) if isinstance(models, dict) else {}
        result.append(PricedUsage(row, _row_cost(row, entry)))
    return result


def _row_cost(row: ModelUsage, entry: object) -> Decimal | None:
    if not isinstance(entry, dict):
        return None
    units = (
        (row.input_tokens, "input_usd_per_million", Decimal(1_000_000)),
        (row.output_tokens, "output_usd_per_million", Decimal(1_000_000)),
        (row.cache_read_tokens, "cache_read_usd_per_million", Decimal(1_000_000)),
        (row.cache_write_tokens, "cache_write_usd_per_million", Decimal(1_000_000)),
        (row.search_result_count, "search_result_usd", Decimal(1)),
    )
    populated = [(count, key, divisor) for count, key, divisor in units if count is not None]
    if not populated:
        return None
    total = Decimal(0)
    for count, key, divisor in populated:
        price = entry.get(key)
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
            return None
        total += Decimal(count) * Decimal(str(price)) / divisor
    return total


def render_usage(config: Config, rows: list[ModelUsage], *, heading: str = "Model usage") -> str:
    priced = price_usage(config, rows)
    if not priced:
        return f"{heading}: no successful model calls recorded."
    totals: dict[tuple[str, str, str, str], list[Decimal | int]] = {}
    unpriced = 0
    for item in priced:
        row = item.usage
        key = (row.payer.value, row.capability, row.provider, row.model)
        bucket = totals.setdefault(key, [0, 0, Decimal(0)])
        bucket[0] = int(bucket[0]) + 1
        if item.cost_usd is None:
            unpriced += 1
            bucket[1] = int(bucket[1]) + 1
        else:
            bucket[2] = Decimal(bucket[2]) + item.cost_usd
    lines = [f"{heading}: {len(rows)} successful call(s)"]
    for (payer, capability, provider, model), (calls, missing, cost) in sorted(totals.items()):
        price = "unpriced" if missing else f"${Decimal(cost):.6f}"
        lines.append(f"- {payer} | {capability} | {provider}/{model}: {calls} call(s), {price}")
    if unpriced:
        lines.append(
            f"{unpriced} call(s) are unpriced because models.toml lacks a required current "
            "unit price; they are not counted as zero-cost."
        )
    return "\n".join(lines)
