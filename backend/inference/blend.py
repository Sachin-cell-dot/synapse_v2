from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum


@dataclass(frozen=True)
class BlendResult:
    forecast_mm: float | None
    weights: dict[str, float]
    status: str
    fallback: str | None


def adaptive_weights(source_ids: Sequence[str], historical_absolute_errors: Mapping[str, Sequence[float]], *, power: float, mae_floor: float) -> tuple[dict[str, float], str | None]:
    if not source_ids:
        return {}, "no_available_sources"
    histories = [historical_absolute_errors.get(source_id, ()) for source_id in source_ids]
    if any(not errors for errors in histories):
        equal = 1.0 / len(source_ids)
        return {source_id: equal for source_id in source_ids}, "equal_weight_no_complete_history"
    scores = {}
    for source_id, errors in zip(source_ids, histories):
        mae = fsum(float(error) for error in errors) / len(errors)
        scores[source_id] = 1.0 / max(mae, mae_floor) ** power
    total = fsum(scores.values())
    return {source_id: score / total for source_id, score in scores.items()}, None


def blend_forecasts(forecasts: Mapping[str, float | None], historical_absolute_errors: Mapping[str, Sequence[float]], *, power: float, mae_floor: float, minimum_sources: int) -> BlendResult:
    available = {source_id: float(value) for source_id, value in forecasts.items() if value is not None}
    if len(available) < minimum_sources:
        return BlendResult(None, {}, "insufficient_sources", "minimum_sources_not_met")
    weights, fallback = adaptive_weights(tuple(available), historical_absolute_errors, power=power, mae_floor=mae_floor)
    forecast = max(0.0, fsum(available[source_id] * weights[source_id] for source_id in available))
    status = "degraded" if len(available) < len(forecasts) else "complete"
    return BlendResult(forecast, weights, status, fallback)
