import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  Divider,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import {
  useDatasetRuns,
  usePinBaseline,
  useUnpinBaseline,
} from "src/api/develop/runs";
import { runColorAt, runColorMap } from "./runColors";
import { runLabel, runTitle } from "./runLabel";
import CustomTooltip from "src/components/tooltip";
import { fmtDateShort } from "src/sections/common/format/fmt";
import { NO_BASELINE, runMatches } from "./runMatch";
import { PickerGroupTitle, RunPickerRow } from "./RunPickerList";

/**
 * BaselinePicker — chooses the baseline run deltas are measured against
 * (PRODUCT-SPEC §4, C18, D3). R3: the trigger reads `Baseline #2A ● ▾`
 * (the id never truncates); the tooltip has the full name and the pin:
 * `#2A Support Ops Reply V1 + tools × gpt-4o-mini · pinned by Ravi · Oct 12`.
 * Groups: `Pinned baseline` · `In this view` · `Other runs on this dataset`
 * (searchable; hidden when `restrictTo` is set) · `None` (absolute mode,
 * when `allowNone`). The footer pins or unpins the dataset baseline (R2).
 *
 * Props (frozen):
 * @param {string} props.datasetId
 * @param {string|null} props.value  run id, or "none"
 * @param {string|null} [props.pinnedRunId]  the dataset's pinned baseline
 * @param {object[]} [props.runsInView]  canonical Runs on the page, in order
 * @param {string[]} [props.restrictTo]  only these run ids are offered
 *   (the experiment page passes its own runs)
 * @param {boolean} [props.allowNone]  offers `None` (Summary tab)
 * @param {(runId: string, meta: {scope: "view"}) => void} props.onChange
 * @param {string} [props.pinnedBy]  R3 optional: who pinned (else the R1
 *   `baseline.pinned_by`)
 * @param {string} [props.pinnedAt]  R3 optional: when (ISO)
 */
const BaselinePicker = ({
  datasetId,
  value,
  pinnedRunId,
  pinnedBy: pinnedByProp,
  pinnedAt: pinnedAtProp,
  runsInView = [],
  restrictTo,
  allowNone = false,
  onChange,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const restricted = Array.isArray(restrictTo) && restrictTo.length > 0;

  const { data, isLoading } = useDatasetRuns(datasetId, {
    enabled: open && !restricted,
  });
  const pin = usePinBaseline(datasetId);
  const unpin = useUnpinBaseline(datasetId);

  const allRuns = useMemo(() => {
    const byId = new Map();
    runsInView.forEach((r) => r?.run_id && byId.set(r.run_id, r));
    (data?.results ?? []).forEach(
      (r) => r?.run_id && !byId.has(r.run_id) && byId.set(r.run_id, r),
    );
    return byId;
  }, [runsInView, data]);

  const colors = useMemo(
    () =>
      runColorMap(
        runsInView.map((r) => r?.run_id),
        value && value !== NO_BASELINE ? value : null,
      ),
    [runsInView, value],
  );
  const colorFor = (id, i) => colors[id] ?? runColorAt(runsInView.length + i);

  const viewIds = runsInView
    .map((r) => r?.run_id)
    .filter((id) => id && (!restricted || restrictTo.includes(id)));
  const pinnedRun = pinnedRunId ? allRuns.get(pinnedRunId) : null;
  const pinnedVisible =
    pinnedRun && (!restricted || restrictTo.includes(pinnedRunId));
  const inView = viewIds
    .filter((id) => id !== pinnedRunId)
    .map((id) => allRuns.get(id))
    .filter(Boolean);
  const others = restricted
    ? []
    : [...allRuns.values()].filter(
        (r) =>
          r.run_id !== pinnedRunId &&
          !viewIds.includes(r.run_id) &&
          runMatches(r, search),
      );

  const current =
    value && value !== NO_BASELINE ? allRuns.get(value) ?? null : null;
  const triggerLabel =
    value === NO_BASELINE
      ? "None"
      : current
        ? runLabel(current)
        : value
          ? "Run not found"
          : "Choose";

  const choose = (runId) => {
    setOpen(false);
    setSearch("");
    if (runId !== value) onChange?.(runId, { scope: "view" });
  };

  // R3 G0 (B11): the trigger reads `Baseline #2A ●` (never truncates the
  // id); the full name and who pinned it live in the tooltip.
  const pinMeta =
    data?.baseline?.run_id === pinnedRunId ? data?.baseline : null;
  const pinnedBy = pinnedByProp ?? pinMeta?.pinned_by ?? null;
  const pinnedAt = pinnedAtProp ?? pinMeta?.pinned_at ?? null;
  const triggerTooltip = current
    ? [
        runTitle(current),
        value === pinnedRunId
          ? `pinned${pinnedBy ? ` by ${String(pinnedBy).split(" ")[0]}` : ""}${pinnedAt ? ` · ${fmtDateShort(pinnedAt)}` : ""}`
          : "this view only (not pinned)",
      ].join(" · ")
    : value === NO_BASELINE
      ? "No baseline: runs are not compared"
      : "Choose the run everything else is compared to";

  const referenceVersion = current?.dataset_version?.number;
  const canPin = current && value !== pinnedRunId;
  const canUnpin = pinnedRunId && value === pinnedRunId;

  return (
    <>
      <CustomTooltip
        show={!open}
        size="small"
        placement="bottom"
        describeChild
        title={triggerTooltip}
      >
        <Button
          ref={anchorRef}
          variant="outlined"
          size="small"
          onClick={() => setOpen(true)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          aria-haspopup="dialog"
          aria-label={`Baseline ${triggerLabel}. Change baseline`}
          sx={{
            px: 1.25,
            height: 32,
            gap: 0.75,
            flexShrink: 0,
            whiteSpace: "nowrap",
          }}
        >
          <Typography
            typography="s1"
            color="text.secondary"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            Baseline
          </Typography>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color={
              value && !current && value !== NO_BASELINE
                ? "error.main"
                : "text.primary"
            }
            noWrap
            sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
          >
            {triggerLabel}
          </Typography>
          {current && (
            <Box
              component="span"
              aria-hidden
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                flexShrink: 0,
                bgcolor: colorFor(current.run_id, 0).solid,
              }}
            />
          )}
        </Button>
      </CustomTooltip>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => {
          setOpen(false);
          setSearch("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              mt: 0.5,
              p: 1,
              borderRadius: 1,
              width: 420,
              maxHeight: 480,
              display: "flex",
              flexDirection: "column",
            },
          },
        }}
      >
        {!restricted && (
          <FormSearchField
            searchQuery={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search #4, name, model"
            autoFocus
            fullWidth
            sx={{ mb: 0.5 }}
          />
        )}
        <Box sx={{ overflowY: "auto", flex: 1, minHeight: 0 }}>
          {pinnedVisible && runMatches(pinnedRun, search) && (
            <>
              <PickerGroupTitle>Pinned baseline</PickerGroupTitle>
              <RunPickerRow
                run={pinnedRun}
                color={colorFor(pinnedRunId, 0)}
                isPinned
                selected={value === pinnedRunId}
                referenceVersionNumber={referenceVersion}
                onToggle={() => choose(pinnedRunId)}
              />
            </>
          )}
          {inView.filter((r) => runMatches(r, search)).length > 0 && (
            <>
              <PickerGroupTitle>
                {restricted ? "Runs in this experiment" : "In this view"}
              </PickerGroupTitle>
              {inView
                .filter((r) => runMatches(r, search))
                .map((r, i) => (
                  <RunPickerRow
                    key={r.run_id}
                    run={r}
                    color={colorFor(r.run_id, i)}
                    selected={value === r.run_id}
                    referenceVersionNumber={referenceVersion}
                    onToggle={() => choose(r.run_id)}
                  />
                ))}
            </>
          )}
          {!restricted && (
            <>
              <PickerGroupTitle>Other runs on this dataset</PickerGroupTitle>
              {isLoading ? (
                <Stack alignItems="center" sx={{ py: 1.5 }}>
                  <CircularProgress size={16} />
                </Stack>
              ) : others.length ? (
                others.map((r, i) => (
                  <RunPickerRow
                    key={r.run_id}
                    run={r}
                    color={colorFor(r.run_id, inView.length + i + 1)}
                    selected={value === r.run_id}
                    referenceVersionNumber={referenceVersion}
                    onToggle={() => choose(r.run_id)}
                  />
                ))
              ) : (
                <Typography
                  typography="s2"
                  color="text.disabled"
                  sx={{ px: 1, py: 0.75 }}
                >
                  {search
                    ? `No runs match “${search}”`
                    : "No other runs on this dataset"}
                </Typography>
              )}
            </>
          )}
          {allowNone && (
            <>
              <Divider sx={{ my: 0.5 }} />
              <ButtonBase
                onClick={() => choose(NO_BASELINE)}
                sx={{
                  width: "100%",
                  justifyContent: "flex-start",
                  gap: 1,
                  px: 1,
                  py: 0.75,
                  borderRadius: 0.5,
                  bgcolor:
                    value === NO_BASELINE ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Box sx={{ width: 16, display: "flex" }}>
                  {value === NO_BASELINE && (
                    <Iconify
                      icon="mdi:check"
                      width={16}
                      sx={{ color: "primary.main" }}
                    />
                  )}
                </Box>
                <Stack alignItems="flex-start">
                  <Typography typography="s1">None</Typography>
                  <Typography typography="s3" color="text.secondary">
                    Show absolute values, no deltas
                  </Typography>
                </Stack>
              </ButtonBase>
            </>
          )}
        </Box>
        {(canPin || canUnpin) && (
          <>
            <Divider sx={{ my: 0.5 }} />
            <ButtonBase
              disabled={pin.isPending || unpin.isPending}
              onClick={() => {
                if (canUnpin) unpin.mutate();
                else pin.mutate(value);
              }}
              sx={{
                justifyContent: "flex-start",
                gap: 1,
                px: 1,
                py: 0.75,
                borderRadius: 0.5,
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify
                icon={canUnpin ? "mdi:pin-off-outline" : "mdi:pin-outline"}
                width={16}
                sx={{ color: "text.secondary" }}
              />
              <Typography typography="s1">
                {canUnpin
                  ? `Unpin ${runLabel(current)} as dataset baseline`
                  : `Pin ${runLabel(current)} as dataset baseline`}
              </Typography>
              {(pin.isPending || unpin.isPending) && (
                <CircularProgress size={12} />
              )}
            </ButtonBase>
          </>
        )}
      </Popover>
    </>
  );
};

BaselinePicker.propTypes = {
  datasetId: PropTypes.string.isRequired,
  value: PropTypes.string,
  pinnedRunId: PropTypes.string,
  pinnedBy: PropTypes.string,
  pinnedAt: PropTypes.string,
  runsInView: PropTypes.arrayOf(PropTypes.object),
  restrictTo: PropTypes.arrayOf(PropTypes.string),
  allowNone: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};

export default BaselinePicker;
