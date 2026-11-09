import { Box, Button, IconButton, Typography } from "@mui/material";
import { fmtCount } from "src/sections/common/format/fmt";
import PropTypes from "prop-types";
import React, { useRef } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show/ShowComponent";
import SvgColor from "src/components/svg-color";
import { getUniqueColorPalette } from "src/utils/utils";
import { useRerunColumnInExperimentStoreShallow } from "./states";
import { EXPERIMENT_COLUMN_STATUS } from "./constants";
import MetricHeader from "src/sections/common/Metrics/MetricHeader";
import RunChip from "src/sections/common/Runs/RunChip";
import {
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";

const iconStyle = {
  color: "text.secondary",
};
export const CustomExperimentColumnHeader = (props) => {
  const { displayName, showColumnMenu, col, hideMenu, head, index } = props;
  const refButton = useRef(null);

  const originType = col?.origin_type ?? col?.originType;
  const dataType = col?.data_type ?? col?.dataType;
  const onMenuClicked = () => {
    showColumnMenu(refButton?.current);
  };

  const setSelectedSourceId = useRerunColumnInExperimentStoreShallow(
    (state) => state?.setSelectedSourceId,
  );
  const renderIcon = () => {
    if (props?.isGrouped) {
      const color = getUniqueColorPalette(index ? index - 1 : 0);
      return (
        <Box
          sx={{
            px: head ? "9px" : "0",
            py: head ? "3px" : "0",
            borderRadius: "4px",
            marginRight: head ? "9px" : "0",
            backgroundColor: color?.tagBackground,
          }}
        >
          <Typography
            variant="s2_1"
            color={color?.tagForeground}
            fontWeight={"fontWeightMedium"}
          >
            {head}
          </Typography>
        </Box>
      );
    } else if (originType === "run_prompt") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_run_prompt.svg`}
          sx={{ width: 20, height: 20, color: "info.main" }}
        />
      );
    } else if (originType === "evaluation") {
      return (
        <Iconify
          icon="material-symbols:check-circle-outline"
          sx={{ color: "info.success" }}
        />
      );
    } else if (
      originType === "optimisation" ||
      originType === "optimisation_evaluation"
    ) {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_optimize.svg`}
          sx={{ width: 20, height: 20, color: "primary.main" }}
        />
      );
    } else if (originType === "annotation_label") {
      return <Iconify icon="jam:write" sx={iconStyle} />;
    } else if (dataType === "text") {
      return <Iconify icon="material-symbols:notes" sx={iconStyle} />;
    } else if (dataType === "array") {
      return <Iconify icon="material-symbols:data-array" sx={iconStyle} />;
    } else if (dataType === "integer") {
      return <Iconify icon="material-symbols:tag" sx={iconStyle} />;
    } else if (dataType === "float") {
      return <Iconify icon="tabler:decimal" sx={iconStyle} />;
    } else if (dataType === "boolean") {
      return (
        <Iconify icon="material-symbols:toggle-on-outline" sx={iconStyle} />
      );
    } else if (dataType === "datetime") {
      return <Iconify icon="tabler:calendar" sx={iconStyle} />;
    } else if (dataType === "json") {
      return <Iconify icon="material-symbols:data-object" sx={iconStyle} />;
    } else if (dataType === "image") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_image.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    } else if (dataType === "audio") {
      return (
        <SvgColor
          src={`/assets/icons/action_buttons/ic_audio.svg`}
          sx={{ width: 20, height: 20, color: "text.secondary" }}
        />
      );
    }
  };

  return (
    <Box
      id="bla"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        "&:hover .rerun-button": {
          display: "flex",
          backgroundColor: "background.neutral !important",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          overflow: "hidden",
        }}
      >
        {renderIcon()}
        <Typography
          fontWeight={700}
          fontSize="13px"
          color={"text.secondary"}
          sx={{
            textOverflow: "ellipsis",
            overflow: "hidden",
          }}
        >
          {displayName}
        </Typography>
      </Box>
      {!hideMenu && (
        <IconButton size="small" ref={refButton} onClick={onMenuClicked}>
          <Iconify icon="mdi:dots-vertical" />
        </IconButton>
      )}
      <ShowComponent condition={originType === "experiment"}>
        <Button
          className="rerun-button"
          startIcon={<SvgColor src="/assets/icons/navbar/ic_evaluate.svg" />}
          variant="outlined"
          size="small"
          color="primary"
          onClick={() => {
            setSelectedSourceId(col?.source_id ?? col?.sourceId);
          }}
          disabled={[
            EXPERIMENT_COLUMN_STATUS.RUNNING,
            EXPERIMENT_COLUMN_STATUS.NOT_STARTED,
            EXPERIMENT_COLUMN_STATUS.QUEUED,
          ].includes(col?.status)}
          sx={{
            display: "none",
            ":hover ": {
              display: "flex",
            },
          }}
        >
          Re run
        </Button>
      </ShowComponent>
    </Box>
  );
};

CustomExperimentColumnHeader.propTypes = {
  displayName: PropTypes.string.isRequired,
  eSort: PropTypes.object,
  eMenu: PropTypes.object,
  eFilterButton: PropTypes.object,
  eFilter: PropTypes.object,
  eSortOrder: PropTypes.object,
  eSortAsc: PropTypes.object,
  eSortDesc: PropTypes.object,
  eSortNone: PropTypes.object,
  eText: PropTypes.object,
  menuButtonRef: PropTypes.object,
  filterButtonRef: PropTypes.object,
  sortOrderRef: PropTypes.object,
  sortAscRef: PropTypes.object,
  sortDescRef: PropTypes.object,
  sortNoneRef: PropTypes.object,
  filterRef: PropTypes.object,
  showColumnMenu: PropTypes.func,
  col: PropTypes.object,
  hideMenu: PropTypes.bool,
  isGrouped: PropTypes.bool,
  head: PropTypes.string,
  index: PropTypes.number,
};

export default CustomExperimentColumnHeader;

// ---------------------------------------------------------------------------
// Results grid headers (PRODUCT-SPEC §3.10.7, M6). AG Grid spreads
// `headerComponentParams` into these props.

/** Eval, human, latency, cost and steps columns: the MetricHeader primitive. */
export const ResultMetricColumnHeader = (props) => (
  <MetricHeader
    def={props.def}
    run={props.run}
    color={props.color}
    aggregate={props.aggregate}
    isBaseline={props.isBaseline}
    partial={props.partial}
    movement={props.movement}
    delta={props.delta}
    onMovement={props.onMovement}
  />
);

ResultMetricColumnHeader.propTypes = {
  def: PropTypes.object,
  run: PropTypes.object,
  color: PropTypes.object,
  aggregate: PropTypes.oneOfType([PropTypes.number, PropTypes.object]),
  isBaseline: PropTypes.bool,
  partial: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
  movement: PropTypes.object,
  delta: PropTypes.object,
  onMovement: PropTypes.func,
};

/** `248 rows · v6`, `62 of 120 rows`, `2 errors`: line 2 of an Output header. */
export function runHeaderLine(run) {
  if (!run || run.not_found) return "";
  const p = runProgressOf(run);
  const info = getRunStatus(run);
  const version = run.dataset_version?.number
    ? `v${run.dataset_version.number}`
    : null;
  // R2D-33: plural through vocab (`1 row · v2`, never `1 rows`).
  const rows =
    info.active || info.waiting
      ? `${fmtCount(p.done)} of ${fmtCount(p.total, "row")}`
      : fmtCount(p.total, "row");
  const errors = p.errors ? fmtCount(p.errors, "error") : null;
  const trials = run.trials > 1 ? `×${fmtCount(run.trials, "trial")}` : null;
  return [rows, version, trials, errors].filter(Boolean).join(" · ");
}

/** Output columns: run chip, then rows · version · trials · errors. */
export const ResultRunColumnHeader = ({ run, color, isBaseline }) => (
  <Box
    sx={{
      display: "flex",
      flexDirection: "column",
      gap: 0.5,
      py: 0.75,
      minWidth: 0,
      width: "100%",
    }}
    onClick={(e) => e.stopPropagation()}
  >
    {run && (
      <Box sx={{ display: "flex", minWidth: 0 }}>
        <RunChip
          run={run}
          color={color}
          isBaseline={isBaseline}
          isPinned={!!run.is_dataset_baseline}
          size="small"
        />
      </Box>
    )}
    <Typography typography="s2" color="text.secondary" noWrap sx={{ pl: 0.25 }}>
      {runHeaderLine(run)}
    </Typography>
  </Box>
);

ResultRunColumnHeader.propTypes = {
  run: PropTypes.object,
  color: PropTypes.object,
  isBaseline: PropTypes.bool,
};

/** Preference columns: `Human · Win 68% (W24 T6 L10)` / `LLM judge · …`. */
export const ResultPreferenceColumnHeader = ({
  source,
  summary,
  candidate,
}) => {
  const s = source === "judge" ? summary?.judge : summary;
  const judged = (s?.wins ?? 0) + (s?.ties ?? 0) + (s?.losses ?? 0);
  const win =
    source === "judge"
      ? judged
        ? (s.wins + 0.5 * s.ties) / judged
        : null
      : summary?.win_rate;
  const agreement = summary?.judge?.agreement;
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        py: 0.75,
        minWidth: 0,
      }}
      onClick={(e) => e.stopPropagation()}
      data-candidate={candidate}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        <Iconify
          icon={
            source === "judge" ? "mdi:robot-outline" : "mdi:account-outline"
          }
          width={14}
          sx={{ color: "text.secondary" }}
        />
        <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
          {source === "judge" ? "LLM judge" : "Human"}
        </Typography>
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        title={
          source === "judge" && agreement?.n
            ? `Agrees with reviewers on ${agreement.agree} of ${agreement.n} rows`
            : summary?.ci?.[0] != null
              ? `95% CI ${Math.round(summary.ci[0] * 100)}–${Math.round(summary.ci[1] * 100)}%`
              : undefined
        }
      >
        {judged
          ? `Win ${Math.round((win ?? 0) * 100)}% (W${s.wins} T${s.ties} L${s.losses})`
          : "No judgements yet"}
      </Typography>
    </Box>
  );
};

ResultPreferenceColumnHeader.propTypes = {
  source: PropTypes.string,
  summary: PropTypes.object,
  candidate: PropTypes.string,
};
