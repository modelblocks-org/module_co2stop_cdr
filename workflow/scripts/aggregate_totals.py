"""Aggregate all CO2Stop groups into one."""

import sys
from typing import TYPE_CHECKING, Any

import _plots
import _schemas
import geopandas as gpd
import pandas as pd
from pyproj import CRS

if TYPE_CHECKING:
    snakemake: Any


def main() -> None:
    """Main snakemake process."""
    proj_crs = snakemake.params.proj_crs
    scenario = snakemake.wildcards.scenario
    if not CRS.from_user_input(proj_crs).is_projected:
        raise ValueError(f"Expected projected CRS, got {proj_crs!r}.")

    shapes = _schemas.ShapeSchema.validate(gpd.read_parquet(snakemake.input.shapes))
    shapes = shapes.to_crs(proj_crs)

    aggregates = []
    for file in snakemake.input.aggregates:
        aggregates.append(_schemas.AggregatedSchema.validate(pd.read_parquet(file)))
    totals: pd.DataFrame = pd.concat(aggregates, axis="index", ignore_index=True)
    totals = totals.groupby("shape_id", as_index=False)["max_sequestered_mtco2"].sum()

    totals.to_parquet(snakemake.output.totals)

    fig, _ = _plots.plot_aggregate(shapes, totals)
    fig.suptitle(f"Total sequestration potential in {scenario!r} scenario")
    fig.savefig(snakemake.output.plot, dpi=300, bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    main()
