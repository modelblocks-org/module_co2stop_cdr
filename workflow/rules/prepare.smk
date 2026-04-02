"""Rules for database preparation."""

CDR_GROUP = ["aquifer", "gas", "oil"]


rule prepare_co2stop_storage_units:
    message:
        "Harmonising CO2Stop storage units: aquifer."
    params:
        cdr_group="aquifer",
        cdr_group_config=lambda wc: config["imputation"]["co2stop"]["aquifer"],
        dataset="storage_units",
        geo_crs=config["crs"]["geographic"],
    input:
        table=rules.unzip_co2stop.output.storage_data,
        polygons=rules.unzip_co2stop.output.storage_map,
        countries=rules.unzip_co2stop.output.country_map,
    output:
        mtco2="<resources>/automatic/co2stop/storage_units/aquifer.parquet",
        plot_kept=report(
            "<resources>/automatic/co2stop/storage_units/aquifer_kept.png",
            caption="../report/prepare_co2stop_kept.rst",
            category="CO2Stop module",
            subcategory="kept polygons",
        ),
        plot_scenarios=report(
            "<resources>/automatic/co2stop/storage_units/aquifer_scenarios.png",
            caption="../report/prepare_co2stop_scenarios.rst",
            category="CO2Stop module",
            subcategory="scenarios",
        ),
    log:
        "<logs>/storage_units/aquifer/prepare_co2stop.log",
    conda:
        "../envs/co2stop.yaml"
    script:
        "../scripts/prepare_co2stop.py"


rule prepare_co2stop_traps:
    message:
        "Harmonising CO2Stop traps: {wildcards.cdr_group}."
    params:
        cdr_group=lambda wc: wc.cdr_group,
        cdr_group_config=lambda wc: config["imputation"]["co2stop"][wc.cdr_group],
        dataset="traps",
        geo_crs=config["crs"]["geographic"],
    input:
        table=rules.unzip_co2stop.output.traps_data,
        polygons=rules.unzip_co2stop.output.traps_map,
        countries=rules.unzip_co2stop.output.country_map,
    output:
        mtco2="<resources>/automatic/co2stop/traps/{cdr_group}.parquet",
        plot_kept=report(
            "<resources>/automatic/co2stop/traps/{cdr_group}_kept.png",
            caption="../report/prepare_co2stop_kept.rst",
            category="CO2Stop module",
            subcategory="kept polygons",
        ),
        plot_scenarios=report(
            "<resources>/automatic/co2stop/traps/{cdr_group}_scenarios.png",
            caption="../report/prepare_co2stop_scenarios.rst",
            category="CO2Stop module",
            subcategory="scenarios",
        ),
    log:
        "<logs>/traps/{cdr_group}/prepare_co2stop.log",
    wildcard_constraints:
        cdr_group="|".join(CDR_GROUP),
    conda:
        "../envs/co2stop.yaml"
    script:
        "../scripts/prepare_co2stop.py"
