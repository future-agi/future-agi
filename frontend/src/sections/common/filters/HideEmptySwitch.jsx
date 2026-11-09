import React, { useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import CustomTooltip from "src/components/tooltip";
import { fetchResultIds, runResultsKey } from "src/api/develop/run-results";
import useResultsUrlState from "src/sections/experiment-detail/results/useResultsUrlState";
import {
  HIDE_EMPTY_LABEL,
  hideEmptyHint,
  hidesEmpty,
  withHideEmpty,
} from "./hideEmpty";
import { useRowFit } from "./rowRoom";

const hiddenTwin = {
  position: "absolute",
  visibility: "hidden",
  pointerEvents: "none",
  top: 0,
  left: 0,
  height: 0,
  overflow: "hidden",
  whiteSpace: "nowrap",
};

/** The labelled switch (the toolbar's, and its hidden measuring twin). */
const Labelled = ({ on, onToggle, twin = false }) => (
  <FormControlLabel
    sx={{ ml: 0, mr: 0, flexShrink: 0, gap: 0.25 }}
    control={
      <Switch
        size="small"
        checked={on}
        onChange={(e) => onToggle?.(e.target.checked)}
        inputProps={
          twin
            ? { tabIndex: -1, "aria-hidden": true }
            : { "aria-label": HIDE_EMPTY_LABEL }
        }
      />
    }
    label={
      <Typography typography="s2" noWrap>
        {HIDE_EMPTY_LABEL}
      </Typography>
    }
  />
);
Labelled.propTypes = {
  on: PropTypes.bool,
  onToggle: PropTypes.func,
  twin: PropTypes.bool,
};

/**
 * HO results R2 (A-H5): `Hide rows with no output` in the results toolbar,
 * single run and compare alike. On, it is the page's expression pill
 * `not (all.output is empty)` (the same pill as the filter dialog's quick
 * pick, so the chip, the count, Copy as API call and saved views all read
 * it); off removes it. The tooltip counts the rows it leaves out, from two
 * R3 `ids_only` reads of the page's view. It always shows with its words:
 * the toolbar ROW is measured (`rowOf()`, its free room beside the
 * right-hand actions or the selection bar, never the viewport) and the
 * switch shows when it fits whole; a full row leaves it out (the View
 * menu's `Show on this page` line is the same pill).
 * Props: datasetId, view (the page's R3 view), rowOf.
 */
const HideEmptySwitch = ({ datasetId, view, rowOf }) => {
  const url = useResultsUrlState();
  const on = hidesEmpty(url.pills);
  const slotRef = useRef(null);
  const twinRef = useRef(null);
  const fits = useRowFit({ rowOf, slotRef, twinRef });
  const base = useMemo(
    () => ({ ...(view ?? {}), pills: withHideEmpty(view?.pills, false) }),
    [view],
  );
  const enabled = Boolean(datasetId) && (view?.runIds?.length ?? 0) > 0;
  const { data: counts } = useQuery({
    queryKey: [...runResultsKey(datasetId), "hide-empty", base],
    queryFn: async () => {
      const [without, withPill] = await Promise.all([
        fetchResultIds(datasetId, base),
        fetchResultIds(datasetId, {
          ...base,
          pills: withHideEmpty(base.pills, true),
        }),
      ]);
      return { without: without.length, with: withPill.length };
    },
    enabled,
    staleTime: 5_000,
  });
  if (!enabled) return null;
  const hint = hideEmptyHint(counts);
  const toggle = (checked) => {
    const next = withHideEmpty(url.pills, checked);
    url.patch({ filters: next.length ? JSON.stringify(next) : null });
  };
  return (
    <>
      <Box ref={twinRef} aria-hidden sx={hiddenTwin}>
        <Labelled on={on} twin />
      </Box>
      {fits ? (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={
            hint ?? "Leaves out the rows where no run in view has an output"
          }
        >
          <Box
            component="span"
            ref={slotRef}
            data-testid="results-hide-empty"
            data-hidden-rows={
              counts ? Math.max(0, counts.without - counts.with) : undefined
            }
            sx={{ display: "inline-flex", flexShrink: 0 }}
          >
            <Labelled on={on} onToggle={toggle} />
          </Box>
        </CustomTooltip>
      ) : null}
    </>
  );
};

HideEmptySwitch.propTypes = {
  datasetId: PropTypes.string,
  view: PropTypes.object,
  rowOf: PropTypes.func,
};

export default HideEmptySwitch;
