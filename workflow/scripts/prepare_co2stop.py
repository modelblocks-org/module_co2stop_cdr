"""Prepare the CO2Stop data so it fits our schemas.

Dataset-wide imputations happen here.
"""

import re
import sys
from typing import TYPE_CHECKING, Any
from warnings import warn

import _schemas
import geopandas as gpd
import numpy as np
import pandas as pd
from _utils import CDR_GROUP, CDRGroup, get_padded_bounds
from cmap import Colormap
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from pyproj import CRS

if TYPE_CHECKING:
    snakemake: Any

# Translate readable config names to CO2Stop columns
MINIMUMS_CO2STOP = {
    "porosity_ratio": "POROSITY_MEAN",
    "depth_m": "DEPTH_MEAN",
    "reservoir_thickness_m": "GROSS_THICK_MEAN",
    "seal_thickness_m": "MIN_SEAL_THICK",
    "permeability_md": "PERM_MEAN",
}


def _surface_issues(df: pd.DataFrame) -> pd.Series:
    """Detect surface issues per row.

    Columns used:
    - SURF_ISSUES: Only empty or 'None' cases are considered safe.
    - REMARKS: additional remarks by CO2Stop authors (e.g., land ownership issues).

    Args:
        df (pd.DataFrame): dataframe with CO2Stop data.

    Returns:
        pd.Series: True if issue is present. False otherwise.
    """
    issues = (
        df["SURF_ISSUES"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .replace({"none": np.nan, "": np.nan})
    )
    unsafe = issues.notna()

    problems = ["surface issue ="]
    pattern = "|".join(re.escape(i) for i in problems)

    flagged_in_remarks = (
        df["REMARKS_DATA"]
        .fillna("")
        .astype(str)
        .str.lower()
        .str.contains(pattern, regex=True)
    )
    unsafe = unsafe | flagged_in_remarks

    return unsafe


def _subsurface_interference_issues(df: pd.DataFrame) -> pd.Series:
    """Detect subsurface issues per row.

    Columns used:
    - SUBSURF_INTERF: Only empty or 'No' cases are considered safe.
    - REMARKS: additional remarks by CO2Stop authors (e.g., groundwater source).

    Args:
        df (pd.DataFrame): dataframe with CO2Stop data.

    Returns:
        pd.Series: True if issue is present. False otherwise.
    """
    subsurface_interf = (
        df["SUBSURF_INTERF"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .replace({"no": np.nan, "": np.nan})
    )
    unsafe = subsurface_interf.notna()

    problems = ["subsurface issue =", "geothermal", "groundwater", "potable water"]
    pattern = "|".join(re.escape(i) for i in problems)
    flagged_in_remarks = (
        df["REMARKS_DATA"]
        .fillna("")
        .astype(str)
        .str.lower()
        .str.contains(pattern, regex=True)
    )
    unsafe |= flagged_in_remarks

    return unsafe


def _artificial_polygon_issues(df: pd.DataFrame) -> pd.Series:
    """Detect cases where the polygon is artificial.

    Uses:
    - REMARKS (from polygons)
    - REMARKS_DATA (from CSV merge, if present)
    """
    checks = [
        (
            "REMARKS",
            [
                "polygon does not represent",
                "polygon in no way represents",
                "arbitrary storage unit polygon",
            ],
        ),
        (
            "REMARKS_DATA",
            [
                "fictive saline aquifer",
                "polygon not available",
                "aproximated polygon",
                "polygon aproximated",
            ],
        ),
    ]

    fake = pd.Series(False, index=df.index)

    for col, problems in checks:
        pattern = "|".join(re.escape(p) for p in problems)
        flagged = (
            df[col].fillna("").astype(str).str.contains(pattern, case=False, regex=True)
        )
        fake |= flagged

    return fake


def _ambiguous_duplicate_issues(df: pd.DataFrame, id_col: str) -> pd.Series:
    """Detect ambiguous cases with repeated IDs.

    All duplicates are eliminated because attribution is uncertain.
    """
    return df[id_col].duplicated(keep=False)


def _removal_warning(mask: pd.Series, name: str, cnf_value) -> None:
    """Issue a warning about the number of 'dropped' elements."""
    if mask.any():
        drops = mask.value_counts()[True] / len(mask)
        warn(f"{name!r}={cnf_value} resulted in {drops:.1%} drops.")


def identify_removals(
    df: pd.DataFrame, remarks: dict, minimums: dict[str, float], id_col: str
) -> pd.Series:
    """Get a mask highlighting rows with problematic qualities as `True`.

    Args:
        df (pd.DataFrame): harmonised CO2Stop dataframe.
        remarks (dict): specifies removal settings that rely on CO2Stop remarks.
        minimums (dict[str, float]): numeric minimums for specific columns.
        id_col (str): column holding a unique per-row identifier.
            Duplicates will be removed.

    Returns:
        pd.Series: resulting mask.
    """
    mask = pd.Series(False, index=df.index)
    # Try to catch problematic remarks
    for remark, setting in remarks.items():
        if setting:
            match remark:
                case "surface_issues":
                    dropped = _surface_issues(df)
                case "subsurface_issues":
                    dropped = _subsurface_interference_issues(df)
                case "artificial_polygons":
                    dropped = _artificial_polygon_issues(df)
                case _:
                    raise KeyError(f"{remark!r} is not valid.")
            _removal_warning(dropped, remark, True)
            mask |= dropped

    # Not optional: these are two small shapes, and skipping it breaks schema validation.
    dropped = _ambiguous_duplicate_issues(df, id_col)
    _removal_warning(dropped, "ambiguous duplicates", "obligatory")
    mask |= dropped

    # Mark rows that violate minimum values
    for config_name, co2stop_col in MINIMUMS_CO2STOP.items():
        min_cnf = minimums[config_name]
        if min_cnf > 0:
            dropped = df[co2stop_col] < min_cnf
            _removal_warning(dropped, config_name, min_cnf)
            mask |= dropped

    return mask


def estimate_storage_scenarios(
    df: pd.DataFrame, cdr_group: CDRGroup, *, lower=float, upper=float("inf")
) -> pd.DataFrame:
    """Get minimum, mean and maximum CO2 capacity per storage unit.

    - Data for minimum, mean, and maximum CO2 capacity is taken from the primary column.
    - If no primary data is present, the fallback column will be used.
    - If at least one value is present (i.e., only mean is given), other categories will
    be filled with it.
    Smaller values are given priority over larger ones.
    E.g.: if min and max are present, mean will be filled with min first.
    - Corrections are applied to ensure monotonic behaviour per row
    (i.e., `conservative <= neutral <= optimistic`).
    """
    if len(cdr_group.primary) != 3:
        raise ValueError(
            "`primary_cols` must have length 3, ordered as min < mean < max."
        )
    out = pd.DataFrame(index=df.index)

    for name, col in cdr_group.primary.items():
        s = df[col].replace(0, np.nan)
        s = s.fillna(df[cdr_group.fallback[name]].replace(0, np.nan))
        out[name] = s

    # Bidirectional propagation within each row
    out = out.ffill(axis="columns").bfill(axis="columns")

    # Enforce lo <= mid <= hi (preserving NaNs)
    lo, mid, hi = list(cdr_group.primary.keys())
    m = out[lo].notna() & out[mid].notna() & (out[lo] > out[mid])
    out[lo] = out[lo].where(~m, out[mid])
    m = out[hi].notna() & out[mid].notna() & (out[hi] < out[mid])
    out[hi] = out[hi].where(~m, out[mid])

    # Set bounds
    out = out.clip(upper=upper)
    out = out.mask(out < lower, 0)

    return out


def harmonise_stopco2_dataset(
    map_file: str, data_file: str, id_col: str, crs: str | int, suffix: str = "_DATA"
) -> gpd.GeoDataFrame:
    """Open and combine paired CO2Stop datasets."""
    gdf = gpd.read_file(map_file).rename({"id": id_col}, axis="columns").to_crs(crs)
    gdf.geometry = gdf.geometry.force_2d().make_valid()
    return gdf.merge(
        pd.read_csv(data_file), how="inner", on=id_col, suffixes=("", suffix)
    )


def plot_kept_polygons(
    countries: gpd.GeoDataFrame,
    all_polygons: gpd.GeoDataFrame,
    kept_polygons: pd.Series,
    *,
    cmap: str = "tol:high_contrast_alt_r",
) -> tuple[Figure, Axes]:
    """Show a visual summary of kept/removed cases."""
    fig, ax = plt.subplots(layout="constrained")
    countries.plot(color="grey", alpha=0.5, ax=ax)
    countries.boundary.plot(color="black", lw=0.5, ax=ax)
    polygons = all_polygons.copy()
    polygons["kept"] = polygons[kept_polygons.name].isin(kept_polygons)
    polygons.plot("kept", legend=True, ax=ax, cmap=Colormap(cmap).to_mpl())
    x_lim, y_lim = get_padded_bounds(all_polygons, pad_frac=0.02)
    ax.set_xlim(*x_lim)
    ax.set_ylim(*y_lim)
    ax.set_axis_off()
    return fig, ax


def plot_scenarios(data: pd.DataFrame) -> tuple[Figure, list[Axes]]:
    """Show a quick comparison between each scenario."""
    axes: list[Axes]
    fig, axes = plt.subplots(1, 2, figsize=(8, 4), layout="constrained")

    scen_names = data.columns.str.split("_", n=1).str[0].tolist()
    axes[0].bar(x=scen_names, height=data.sum().values)
    axes[0].set_title("Aggregate")
    tmp = data.T.copy()
    tmp.index = scen_names
    tmp.plot(ax=axes[1], legend=False, color="grey", alpha=0.5, marker="o")
    axes[1].set_title("Per polygon")
    for ax in axes:
        ax.set_ylabel("$MtCO_2$")
        ax.tick_params(axis="x", which="both", length=0)
    return fig, axes


def main() -> None:
    """Main snakemake process."""
    geo_crs = snakemake.params.geo_crs
    if not CRS.from_user_input(geo_crs).is_geographic:
        raise ValueError(f"Expected geographic CRS, got {geo_crs!r}.")

    dataset_name = snakemake.params.dataset
    cdr_group = snakemake.params.cdr_group
    config = snakemake.params.cdr_group_config

    match dataset_name:
        case "storage_units":
            data_id = "STORAGE_UNIT_ID"
            id_columns = {data_id: "storage_unit_id"}
            validation_method = _schemas.StorageUnitsSchema.validate
        case "traps":
            data_id = "TRAP_ID"
            id_columns = {data_id: "trap_id", "STORAGE_UNIT_ID": "storage_unit_id"}
            validation_method = _schemas.TrapsSchema.validate
        case _:
            raise ValueError(f"Invalid dataset requested: {dataset_name!r}.")

    dataset = harmonise_stopco2_dataset(
        snakemake.input.polygons, snakemake.input.table, data_id, geo_crs
    )
    # Keep a copy of the full dataset, to help display what has been dropped.
    all_polygons = dataset[[data_id, "geometry"]].copy()

    # Identify and remove 'bad apples', depending on the configuration.
    mask_issues = identify_removals(
        dataset, config["remove_remarks"], config["minimums"], data_id
    )
    dataset = dataset[~mask_issues]

    # Estimate storage capacity, keeping only rows with tangible values.
    capacity_scenarios = estimate_storage_scenarios(
        dataset, CDR_GROUP[cdr_group], **config["bounds_mtco2"]
    )
    capacity_cols = capacity_scenarios.columns
    dataset = dataset.merge(
        capacity_scenarios, how="inner", right_index=True, left_index=True
    )
    dataset = dataset.dropna(subset=capacity_cols, how="all")

    # Plot omissions
    countries = gpd.read_file(snakemake.input.countries).to_crs(geo_crs)
    fig, ax = plot_kept_polygons(countries, all_polygons, dataset[data_id])
    ax.set_title(f"Kept polygons for '{dataset_name}:{cdr_group}'.")
    fig.savefig(snakemake.output.plot_kept, dpi=300, bbox_inches="tight")
    # Plot scenarios
    fig, _ = plot_scenarios(dataset[capacity_cols])
    fig.suptitle(
        f"Full CO2Stop dataset scenario comparison for '{dataset_name}:{cdr_group}'"
    )
    fig.savefig(snakemake.output.plot_scenarios, dpi=300, bbox_inches="tight")

    # Remove unnecessary columns, add extra metadata, validate, save
    final_cols = list(id_columns.keys()) + capacity_cols.to_list() + ["geometry"]
    dataset = dataset[final_cols].copy()
    dataset = dataset.rename(id_columns, axis="columns").reset_index(drop=True)
    dataset["dataset"] = dataset_name
    dataset["cdr_group"] = cdr_group
    dataset = validation_method(dataset)
    dataset.to_parquet(snakemake.output.mtco2)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w")
    main()
