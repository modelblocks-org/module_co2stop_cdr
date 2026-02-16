"""Aggregate CO2Stop to provided shapes."""

import sys
from typing import TYPE_CHECKING, Any

import _plots
import _schemas
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS

if TYPE_CHECKING:
    snakemake: Any


def build_scenario_gdf(
    storage_units_file: str, traps_file: str, *, scenario: str, cdr_group: str
) -> gpd.GeoDataFrame:
    """Load and combine requested datasets into a scenario.

    Args:
        storage_units_file (str): path to storage unit dataset.
        traps_file (str): path to traps dataset.
        scenario (str): scenario name. One of: low, medium, high.
        cdr_group (str): CDR grouping. One of: aquifer, oil, gas.

    Raises:
        ValueError: the dataset / group combination lead to an empty scenario.

    Returns:
        gpd.GeoDataFrame: resulting scenario combination.
    """
    cols = [f"{scenario}_mtco2", "dataset", "cdr_group", "geometry"]

    traps = _schemas.TrapsSchema.validate(gpd.read_parquet(traps_file))
    storage_units = _schemas.StorageUnitsSchema.validate(
        gpd.read_parquet(storage_units_file)
    )
    # Always remove traps already represented by storage_units
    traps = traps.loc[~traps["storage_unit_id"].isin(storage_units["storage_unit_id"])]

    # Concatenate if necessary
    if cdr_group == "aquifer":
        scenario_gdf = gpd.GeoDataFrame(
            pd.concat([storage_units[cols], traps[cols]], ignore_index=True),
            geometry="geometry",
            crs=storage_units.crs,
        )
    else:
        scenario_gdf = traps[cols].reset_index(drop=True).copy()

    mismatch = set(scenario_gdf["cdr_group"].unique()) ^ set([cdr_group])
    if mismatch:
        raise ValueError(f"Expected only {cdr_group!r}, got {mismatch!r}.")

    scenario_gdf["scenario_id"] = scenario_gdf.index
    scenario_gdf = scenario_gdf.rename({f"{scenario}_mtco2": "mtco2"}, axis="columns")
    return scenario_gdf


def aggregate_scenario_into_shapes(
    shapes: gpd.GeoDataFrame,
    scenario_gdf: gpd.GeoDataFrame,
    *,
    lower: float = 0,
    upper: float = float("inf"),
) -> pd.DataFrame:
    """Overlay scenario polygons with target shapes and MtCO2 per area."""
    if (not shapes.crs.is_projected) or (not shapes.crs.equals(scenario_gdf.crs)):
        raise ValueError("Provided files must share a projected CRS.")

    overlay = gpd.overlay(
        shapes[["shape_id", "geometry"]],
        scenario_gdf,
        how="intersection",
        keep_geom_type=False,
    )

    overlay["piece_area"] = overlay.area
    overlay = overlay.loc[overlay["piece_area"] > 0].copy()

    scenario_area = scenario_gdf.area
    overlay["source_area"] = overlay["scenario_id"].map(scenario_area)
    overlay = overlay.loc[overlay["source_area"] > 0].copy()

    overlay["max_sequestered_mtco2"] = (
        overlay["mtco2"] * overlay["piece_area"] / overlay["source_area"]
    )

    result = overlay.groupby(["shape_id", "cdr_group"], as_index=False).agg(
        max_sequestered_mtco2=("max_sequestered_mtco2", "sum")
    )
    # Set bounds
    tmp = result["max_sequestered_mtco2"].clip(upper=upper)
    result["max_sequestered_mtco2"] = tmp.mask(tmp < lower, np.nan)
    result = result.dropna(subset=["max_sequestered_mtco2"], how="any").reset_index(
        drop=True
    )
    return result


def main() -> None:
    """Main snakemake process."""
    proj_crs = snakemake.params.proj_crs
    cdr_group = snakemake.wildcards.cdr_group
    scenario = snakemake.wildcards.scenario
    if not CRS.from_user_input(proj_crs).is_projected:
        raise ValueError(f"Expected projected CRS, got {proj_crs!r}.")

    shapes = _schemas.ShapeSchema.validate(gpd.read_parquet(snakemake.input.shapes))
    shapes = shapes.to_crs(proj_crs)

    scenario_gdf = build_scenario_gdf(
        storage_units_file=snakemake.input.storage_units,
        traps_file=snakemake.input.traps,
        scenario=scenario,
        cdr_group=cdr_group,
    )

    bounds = snakemake.params.bounds_mtco2
    aggregated = aggregate_scenario_into_shapes(
        shapes=shapes, scenario_gdf=scenario_gdf.to_crs(proj_crs), **bounds
    )
    aggregated = _schemas.AggregatedSchema.validate(aggregated)
    aggregated.to_parquet(snakemake.output.aggregated)

    fig, _ = _plots.plot_aggregate(shapes, aggregated)
    fig.suptitle(f"Sequestration potential for {cdr_group!r} in {scenario!r} scenario")
    fig.savefig(snakemake.output.plot, dpi=300, bbox_inches="tight")


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    main()
