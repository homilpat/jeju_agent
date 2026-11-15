"""RouteResult — routing output contract (ARCHITECTURE §4).

Status invariants (enforced at construction so no layer can emit a
self-contradictory result):

* OK / UNVERIFIED  -> non-empty ``route_edge_ids`` and ``length_m > 0``. An edge may
  repeat (e.g. out-and-back to a POI on a dead end); continuity and repetition
  are checked against the graph by ``validation.route``.
* NO_FEASIBLE_ROUTE / DATA_UNAVAILABLE -> no edges, no length, no duration.
* Any status other than OK must carry at least one caveat explaining why
  (e.g. UNKNOWN accessibility edges, missing data source).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from jeju_lifepath.schemas.common import FrozenModel, NonEmptyStr, RouteStatus, UnitInterval

ROUTED_STATUSES = frozenset({RouteStatus.OK, RouteStatus.UNVERIFIED})


class RouteResult(FrozenModel):
    status: RouteStatus
    route_edge_ids: tuple[NonEmptyStr, ...] = ()
    length_m: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] | None = None
    estimated_minutes: Annotated[float, Field(ge=0.0, allow_inf_nan=False)] | None = None
    # indicator name -> aggregated [0,1] value along the route (None = unavailable)
    indicator_breakdown: dict[NonEmptyStr, UnitInterval | None] = Field(default_factory=dict)
    # indicator name -> share of route length with observed data
    coverage: dict[NonEmptyStr, UnitInterval] = Field(default_factory=dict)
    provenance: tuple[NonEmptyStr, ...] = ()
    # Route edges whose constraint verdict is UNKNOWN (reason for UNVERIFIED).
    unverified_edge_ids: tuple[NonEmptyStr, ...] = ()
    # Detour-bound reference and weight version, kept for re-validation/reproducibility.
    shortest_feasible_m: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] | None = None
    weights_version: NonEmptyStr | None = None
    # User-facing Korean sentences only — no ids, enum codes or JSON (presentation layer).
    caveats: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _status_invariants(self) -> RouteResult:
        if self.status in ROUTED_STATUSES:
            if not self.route_edge_ids:
                raise ValueError(f"{self.status} requires a non-empty route_edge_ids")
            if self.length_m is None:
                raise ValueError(f"{self.status} requires length_m")
        else:
            if self.route_edge_ids or self.length_m is not None or self.estimated_minutes:
                raise ValueError(f"{self.status} must not carry route edges, length or duration")
            if self.indicator_breakdown:
                raise ValueError(f"{self.status} must not carry an indicator breakdown")
        if self.status is not RouteStatus.OK and not self.caveats:
            raise ValueError(f"{self.status} requires at least one caveat explaining the status")
        return self
