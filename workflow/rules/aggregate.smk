"""Rules for shape aggregation."""


rule aggregate_co2stop:
    message:
        "Aggregating '{wildcards.shapes}-{wildcards.scenario}-{wildcards.cdr_group}'."
    params:
        bounds_mtco2=lambda wc: config["imputation"]["aggregated"][wc.cdr_group][
            "bounds_mtco2"
        ],
        proj_crs=config["crs"]["projected"],
    input:
        shapes="<user_shapes>",
        storage_units=rules.prepare_co2stop_storage_units.output.mtco2,
        traps=rules.prepare_co2stop_traps.output.mtco2,
    output:
        aggregated="<cdr_group>",
        plot=report(
            "<results>/{shapes}/{scenario}/{cdr_group}.png",
            caption="../report/aggregate_co2stop.rst",
            category="CO2Stop module",
            subcategory="aggregated {cdr_group}",
        ),
    log:
        "<logs>/{shapes}/{scenario}/{cdr_group}/aggregate_co2stop.log",
    wildcard_constraints:
        scenario="|".join(["low", "medium", "high"]),
        cdr_group="|".join(CDR_GROUP),
    conda:
        "../envs/co2stop.yaml"
    script:
        "../scripts/aggregate_co2stop.py"


rule aggregate_totals:
    message:
        "Aggregating totals for '{wildcards.shapes}-{wildcards.scenario}'."
    params:
        proj_crs=config["crs"]["projected"],
    input:
        shapes="<user_shapes>",
        aggregates=lambda wc: expand(
            rules.aggregate_co2stop.output.aggregated,
            shapes=wc.shapes,
            scenario=wc.scenario,
            cdr_group=CDR_GROUP,
        ),
    output:
        totals="<total_aggregate>",
        plot=report(
            "<results>/{shapes}/{scenario}/totals.png",
            caption="../report/aggregate_co2stop.rst",
            category="CO2Stop module",
            subcategory="aggregated totals",
        ),
    log:
        "<logs>/{shapes}/{scenario}/totals/aggregate_co2stop.log",
    wildcard_constraints:
        scenario="|".join(["low", "medium", "high"]),
    conda:
        "../envs/co2stop.yaml"
    script:
        "../scripts/aggregate_totals.py"
