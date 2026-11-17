import React, { useLayoutEffect, useMemo, useRef } from "react";
import PropTypes from "prop-types";
import { Box, FormControlLabel, Switch, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import CustomTooltip from "src/components/tooltip";
import { fetchResultIds, runResultsKey } from "src/api/develop/run-results";
import { isPrototypeMode } from "src/utils/prototype";
import useResultsUrlState from "src/sections/experiment-detail/results/useResultsUrlState";
import {
  HIDE_EMPTY_LABEL,
  canCountHideEmpty,
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
 * The labelled switch alone (no count read, no row measuring): the results
 * page shows it under a failed read, so the switch can still be turned off.
 */
export const HideEmptyToggle = ({ on, onToggle }) => (
  <Labelled on={on} onToggle={onToggle} />
);
HideEmptyToggle.propTypes = {
  on: PropTypes.bool,
  onToggle: PropTypes.func,
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
 * menu's `Show on this page` line is the same pill). The count is asked
 * only where the read can answer it (canCountHideEmpty: the results
 * endpoint refuses the expression), and a refused count is never a toast:
 * the tooltip then keeps its plain words.
 * Props: datasetId, view (the page's R3 view), rowOf.
 * onLeftOut (optional) is told true while a full row leaves the switch out
 * and false otherwise, so the page can carry the line elsewhere.
 */
const HideEmptySwitch = ({ datasetId, view, rowOf, onLeftOut }) => {
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
  const countable = canCountHideEmpty(base.pills, {
    prototype: isPrototypeMode(),
  });
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
    enabled: enabled && countable,
    staleTime: 5_000,
    retry: false,
    // An optional count: a refused read leaves the tooltip's plain words.
    meta: { errorHandled: true },
  });
  // Left out for room: measured, and it does not fit (not before the first
  // measure, nor with no run in view). Told in the commit that hides or
  // shows the switch, so whatever carries the line instead changes with it.
  const leftOut = enabled && fits === false;
  const onLeftOutRef = useRef(onLeftOut);
  onLeftOutRef.current = onLeftOut;
  useLayoutEffect(() => {
    const tell = onLeftOutRef.current;
    tell?.(leftOut);
    return leftOut ? () => tell?.(false) : undefined;
  }, [leftOut]);
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
  onLeftOut: PropTypes.func,
};

export default HideEmptySwitch;
