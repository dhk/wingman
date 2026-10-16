"""Read and price the content-free model usage ledger."""

from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from wingman.domain.model_usage import ModelUsage, Payer
from wingman.infrastructure.config import Config
from wingman.infrastructure.usage_ledger import read_usage

if TYPE_CHECKING:
    from wingman.infrastructure.tenants import Tenant


@dataclass(frozen=True)
class PricedUsage:
    usage: ModelUsage
    cost_usd: Decimal | None


def render_tenant_usage(tenant: Tenant, limit: int) -> tuple[str, bool]:
    """Read one tenant without creating its DB or hiding later tenants on failure."""
    try:
        config = Config(data_dir=tenant.data_dir, data_dir_source="read-only usage report")
        if not config.db_path.exists():
            # A registered tenant who has not made a workspace yet has spent
            # nothing; that is a fact to report, not a read failure.
            return f"Tenant {tenant.slug}: no usage yet (no workspace database yet).", False
        read = read_usage(config.db_path, limit)
        if not read.present:
            return f"Tenant {tenant.slug}: no usage recorded (ledger not yet created).", False
        report = render_usage(
            config,
            read.rows,
            heading=f"Tenant {tenant.slug}",
            total_count=read.total,
            offline_count=read.offline_count,
        )
        return report, False
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
    if row.payer is Payer.OFFLINE:
        return Decimal(0)
    if not isinstance(entry, dict):
        return None
    # Provider/model identifiers are durable billing provenance. Match them
    # exactly: Wingman has no authoritative alias table, so normalization
    # would risk pricing an old model with a different model's current rate.
    aliases = entry.get("pricing_models", [])
    allowed = (
        aliases
        if isinstance(aliases, list) and all(isinstance(item, str) for item in aliases)
        else []
    )
    if entry.get("provider") != row.provider or row.model not in [entry.get("model"), *allowed]:
        return None
    units = (
        (row.input_tokens, "input_usd_per_million", Decimal(1_000_000)),
        (row.output_tokens, "output_usd_per_million", Decimal(1_000_000)),
        (row.cache_read_tokens, "cache_read_usd_per_million", Decimal(1_000_000)),
        (row.cache_write_tokens, "cache_write_usd_per_million", Decimal(1_000_000)),
        (row.search_result_count, "search_result_usd", Decimal(1)),
    )
    # A configured billing dimension is part of this model's known price
    # formula. If the provider omitted that counter, the usage is unknown —
    # not zero — so a partial total would understate spend.
    if any(count is None and key in entry for count, key, _divisor in units):
        return None
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


COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "search_result_count",
)


def render_usage(
    config: Config,
    rows: list[ModelUsage],
    *,
    heading: str = "Model usage",
    total_count: int | None = None,
    offline_count: int = 0,
) -> str:
    """Billable calls grouped by day and payer, offline calls on one line apart.

    'rows' may include offline rows (they are split out here); 'total_count'
    and 'offline_count', when given, are the ledger's full counts, and
    offline rows in 'rows' are not counted again.
    """
    offline_rows = [row for row in rows if row.payer is Payer.OFFLINE]
    rows = [row for row in rows if row.payer is not Payer.OFFLINE]
    offline_total = offline_count + len(offline_rows)
    priced, diagnostic = _price_usage(config, rows)
    total = len(rows) if total_count is None else total_count
    scope = f"; newest {len(rows)} of {total} (truncated)" if total > len(rows) else ""
    lines = [f"{heading}: {len(rows)} billable call(s){scope}"]
    if not rows:
        lines = [
            f"{heading}: no billable model calls recorded."
            if offline_total
            else f"{heading}: no successful model calls recorded."
        ]
    groups: dict[tuple[str, str, str, str, str], list[PricedUsage]] = {}
    for item in priced:
        r = item.usage
        groups.setdefault(
            (r.recorded_at.date().isoformat(), r.payer.value, r.capability, r.provider, r.model), []
        ).append(item)
    for (day, payer, capability, provider, model), items in sorted(groups.items()):
        costs = [item.cost_usd for item in items]
        known = sum((cost for cost in costs if cost is not None), Decimal(0))
        price = (
            f"${known:.6f}"
            if all(cost is not None for cost in costs)
            else f"unpriced (priced subtotal ${known:.6f})"
        )
        units = []
        for name in COUNTERS:
            values = [getattr(item.usage, name) for item in items]
            subtotal = sum(value for value in values if value is not None)
            value_text = (
                str(subtotal)
                if all(value is not None for value in values)
                else f"unknown (known subtotal {subtotal})"
            )
            units.append(f"{name}={value_text}")
        lines.append(
            f"- {day} | {payer} | {capability} | {provider}/{model}: {len(items)} call(s), {price}; "
            + ", ".join(units)
        )
    if offline_total:
        # Owner decision 2026-10-16 (option b): one line, no payer, no price,
        # never unpriced and never part of any billable total above.
        lines.append(f"- no cost (offline): {offline_total} call(s)")
    if diagnostic and rows:
        lines.append(
            f"Pricing unavailable: {diagnostic}. Raw usage remains available; these calls are unpriced, not zero-cost."
        )
    if any(item.cost_usd is None for item in priced):
        lines.append(
            "Unpriced calls: no matching provider/model price (use explicit pricing_models for returned IDs), counter not reported, missing rate, or invalid rate; not counted as zero-cost."
        )
    lines.append(
        "Accounting is best-effort: failed ledger writes can leave totals a lower bound. These estimates cannot enforce a budget."
    )
    return "\n".join(lines)


class UsageReadError(Exception):
    """The workspace database could not be read for a usage report."""


def render_workspace_usage(config: Config, limit: int) -> str:
    """Own-workspace report; raises UsageReadError, never a raw sqlite error."""
    try:
        read = read_usage(config.db_path, limit)
    except sqlite3.Error as exc:
        raise UsageReadError(
            f"Model usage could not be read from {config.db_path} ({exc}). Nothing was changed; "
            "if the database is busy, retry in a moment; if it is damaged, restore it from a backup."
        ) from exc
    if not read.present:
        return "Model usage: no usage recorded (ledger not yet created)."
    return render_usage(config, read.rows, total_count=read.total, offline_count=read.offline_count)


def funded_ranking(tenants: list[Tenant], limit: int) -> str:
    """Rank known funded spend in the selected rows; never call it a complete bill."""
    ranked: list[tuple[Decimal, str, bool]] = []
    for tenant in tenants:
        try:
            config = Config(data_dir=tenant.data_dir, data_dir_source="read-only usage report")
            if not config.db_path.exists():
                ranked.append((Decimal(0), tenant.slug, False))
                continue
            # read_usage already excludes offline rows from rows and total.
            rows, total, _present, _offline = read_usage(config.db_path, limit)
            funded = [
                item for item in price_usage(config, rows) if item.usage.payer is Payer.FUNDED
            ]
            cost = sum((item.cost_usd for item in funded if item.cost_usd is not None), Decimal(0))
            ranked.append(
                (
                    cost,
                    tenant.slug,
                    total > len(rows) or any(item.cost_usd is None for item in funded),
                )
            )
        except Exception:  # noqa: BLE001, S112 — errors are reported by the detail reader
            continue
    lines = ["Funded spend ranking (known priced subtotal in selected rows; lower bound):"]
    for cost, slug, incomplete in sorted(ranked, key=lambda item: (-item[0], item[1])):
        lines.append(f"- {slug}: ${cost:.6f}" + (" [incomplete]" if incomplete else ""))
    return "\n".join(lines)
