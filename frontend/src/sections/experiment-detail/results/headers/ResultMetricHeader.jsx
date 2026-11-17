import React from "react";
import PropTypes from "prop-types";
import MetricHeader from "src/sections/common/Metrics/MetricHeader";
import { naNoteOf } from "src/sections/common/Runs/scoreStatus";

/**
 * IT-R4 G3 (P1): the results grid's metric column header. The same
 * MetricHeader as `ResultMetricColumnHeader` plus the delta's basis
 * (`on 9 shared`) when the shared rows are not both runs' own rows.
 */
const ResultMetricHeader = (props) => (
  <MetricHeader
    def={props.def}
    run={props.run}
    color={props.color}
    aggregate={props.aggregate}
    isBaseline={props.isBaseline}
    partial={props.partial}
    movement={props.movement}
    delta={props.delta}
    basis={props.basis ?? undefined}
    onMovement={props.onMovement}
    note={props.versionNote ?? naNoteOf(props.counts) ?? undefined}
    ruleLabel={props.ruleLabel ?? undefined}
  />
);

ResultMetricHeader.propTypes = {
  def: PropTypes.object,
  run: PropTypes.object,
  color: PropTypes.object,
  aggregate: PropTypes.oneOfType([PropTypes.number, PropTypes.object]),
  isBaseline: PropTypes.bool,
  partial: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
  movement: PropTypes.object,
  delta: PropTypes.object,
  basis: PropTypes.object,
  onMovement: PropTypes.func,
  counts: PropTypes.object,
  versionNote: PropTypes.object,
  ruleLabel: PropTypes.string,
};

export default ResultMetricHeader;
