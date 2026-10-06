"""Read and price the content-free model usage ledger."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from wingman.domain.model_usage import ModelUsage
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage

if TYPE_CHECKING:
    from wingman.infrastructure.tenants import Tenant


@dataclass(frozen=True)
class PricedUsage:
    usage: ModelUsage
    cost_usd: Decimal | None


def render_tenant_usage(tenant: Tenant, limit: int) -> tuple[str, bool]:
    """Read one tenant without creating its DB or hiding later tenants on failure."""
    try:
        config = tenant.config()
        if not config.db_path.exists():
            return f"Tenant {tenant.slug}: no workspace yet ({config.db_path} missing).", True
        with Storage(config.db_path) as storage:
            return (
                render_usage(
                    config,
                    storage.list_model_usage(limit=limit),
                    heading=f"Tenant {tenant.slug}",
                ),
                False,
            )
    except Exception as exc:  # noqa: BLE001 — one bad tenant must not hide the rest
        return f"Tenant {tenant.slug}: could not be read — {exc}", True


def price_usage(config: Config, rows: list[ModelUsage]) -> list[PricedUsage]:
    """Apply current workspace prices without changing the immutable raw units."""
    priced, _diagnostic = _price_usage(config, rows)
    return priced


def _price_usage(config: Config, rows: list[ModelUsage]) -> tuple[list[PricedUsage], str | None]:
    try:
        models = tomllib.loads(config.models_config_path.read_text(encoding="utf-8")).get(
            "models", {}
        )
        diagnostic = None
    except (FileNotFoundError, NotADirectoryError):
        models = {}
        diagnostic = None
    except PermissionError as exc:
        models = {}
        diagnostic = f"models.toml could not be read (permission denied: {exc})"
    except OSError as exc:
        models = {}
        diagnostic = f"models.toml could not be read ({exc})"
    except tomllib.TOMLDecodeError as exc:
        models = {}
        diagnostic = f"models.toml is malformed ({exc})"
    result: list[PricedUsage] = []
    for row in rows:
        entry = models.get(row.capability, {}) if isinstance(models, dict) else {}
        result.append(PricedUsage(row, _row_cost(row, entry)))
    return result, diagnostic


def _row_cost(row: ModelUsage, entry: object) -> Decimal | None:
    if not isinstance(entry, dict):
        return None
    # Provider/model identifiers are durable billing provenance. Match them
    # exactly: Wingman has no authoritative alias table, so normalization
    # would risk pricing an old model with a different model's current rate.
    if entry.get("provider") != row.provider or entry.get("model") != row.model:
        return None
    units = (
        (row.input_tokens, "input_usd_per_million", Decimal(1_000_000)),
        (row.output_tokens, "output_usd_per_million", Decimal(1_000_000)),
        (row.cache_read_tokens, "cache_read_usd_per_million", Decimal(1_000_000)),
        (row.cache_write_tokens, "cache_write_usd_per_million", Decimal(1_000_000)),
        (row.search_result_count, "search_result_usd", Decimal(1)),
    )
    reported = [(count, key, divisor) for count, key, divisor in units if count is not None]
    if not reported:
        return None
    populated = [(count, key, divisor) for count, key, divisor in reported if count != 0]
    total = Decimal(0)
    for count, key, divisor in populated:
        price = entry.get(key)
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            return None
        unit_price = Decimal(str(price))
        if not unit_price.is_finite() or unit_price < 0:
            return None
        total += Decimal(count) * unit_price / divisor
    return total


def render_usage(config: Config, rows: list[ModelUsage], *, heading: str = "Model usage") -> str:
    priced, pricing_diagnostic = _price_usage(config, rows)
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
    if pricing_diagnostic:
        lines.append(
            f"Pricing unavailable: {pricing_diagnostic}. Raw usage remains available; "
            "these calls are unpriced, not zero-cost."
        )
    elif unpriced:
        lines.append(
            f"{unpriced} call(s) are unpriced because models.toml lacks a required current "
            "unit price; they are not counted as zero-cost."
        )
    return "\n".join(lines)
