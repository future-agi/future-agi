import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RunChip from "../Runs/RunChip";
import DeltaBadge from "./DeltaBadge";
import MovementButtons from "./MovementButtons";
import { formatValue, movementSummaryText } from "./metricFormat";
import { reducerLabel } from "src/sections/common/Analysis/trialReducers";

const num = (v) =>
  typeof v === "number" && Number.isFinite(v) ? v : undefined;

/**
 * MetricHeader — the two-line results-grid header for one run's metric
 * column (PRODUCT-SPEC §3.10.7, M6). Usable as an AG Grid
 * `headerComponent` (pass these as `headerComponentParams`) or inline.
 *   Line 1: RunChip · ⋮ menu
 *   Line 2: aggregate · DeltaBadge · MovementButtons (non-baseline only)
 * The aggregate's tooltip carries the whole story: "Within noise (95% CI
 * ±3.8 pts) · 14 improved · 7 regressed · 215 unchanged · 12 not
 * comparable · Partial · 142 of 248 rows".
 *
 * Props (frozen):
 * @param {object} props.def  metric def
 * @param {object} props.run  canonical Run
 * @param {object} [props.color]  RunColor from useRunColors
 * @param {number|{value: number, agg?: string}} [props.aggregate]
 * @param {boolean} [props.isBaseline]  hides the delta and movement
 * @param {boolean|{done: number, total: number}} [props.partial]  running:
 *   the aggregate turns grey
 * @param {{improved: number, regressed: number, unchanged?: number,
 *   not_comparable?: number, active?: "improved"|"regressed"|null}} [props.movement]
 * @param {{delta: number, ci95?: number, significant?: boolean, n?: number,
 *   vsLabel?: string}} [props.delta]
 * @param {Array<{label: string, icon?: string, onClick: Function,
 *   disabled?: boolean, divider?: boolean}>} [props.menuItems]  ⋮ menu
 * @param {(dir: "improved"|"regressed") => void} [props.onMovement]
 * @param {string|{label: string, tooltip?: string}} [props.basis]  IT-R4
 *   G3 (additive): the rows the delta is on when they are not the runs'
 *   own rows (`on 9 shared`); replaces the `n=9` suffix. Default: none.
 * @param {{label: string, tooltip?: string, tone?: "na"|"muted",
 *   actionLabel?: string, onAction?: Function,
 *   actions?: Array<{label: string, onClick: Function}>}} [props.note]
 *   W1-4 / W1-3 (additive): what the value left out (`9 N/A (45%) · 1
 *   err`), beside the value; its tooltip joins the value's. `actions`
 *   (W1-3: a refused delta's "Re-score #2A on v3" and "Compare anyway")
 *   open from the note's action link as a menu. Default: none.
 * @param {string} [props.ruleLabel]  W1-8 (additive): the rule the value
 *   reads (`pass^3`, `Mean`), shown before it when the runs of one group
 *   read different rules. Default: none.
 */
const MetricHeader = ({
  def,
  run,
  color,
  aggregate,
  isBaseline = false,
  partial = false,
  movement,
  delta,
  menuItems,
  onMovement,
  basis,
  note,
  ruleLabel = null,
}) => {
  const basisLabel = typeof basis === "string" ? basis : basis?.label;
  const basisTip = typeof basis === "string" ? null : basis?.tooltip;
  const [anchor, setAnchor] = useState(null);
  const [noteAnchor, setNoteAnchor] = useState(null);
  const aggValue =
    aggregate && typeof aggregate === "object" ? aggregate.value : aggregate;
  const agg =
    aggregate && typeof aggregate === "object" ? aggregate.agg : undefined;
  // W1-8: a trial reducer's value carries its 95% interval.
  const ci = aggregate && Array.isArray(aggregate.ci95) ? aggregate.ci95 : null;
  const ciText = ci
    ? `(${formatValue(def, ci[0], { agg })}–${formatValue(def, ci[1], { agg })})`
    : null;
  const reducerTip = aggregate?.reducer
    ? `${reducerLabel(aggregate.reducer, def?.trials_k)} of each row's tries${ciText ? ` · 95% interval ${ciText}` : ""}`
    : ciText
      ? `95% interval ${ciText}`
      : null;
  const isPartial = !!partial;
  const done = typeof partial === "object" ? partial.done : undefined;
  const total = typeof partial === "object" ? partial.total : undefined;

  const summary = movementSummaryText(def, {
    ci95: isBaseline ? undefined : num(delta?.ci95),
    significant: isBaseline ? undefined : delta?.significant,
    improved: isBaseline ? undefined : num(movement?.improved),
    regressed: isBaseline ? undefined : num(movement?.regressed),
    unchanged: isBaseline ? undefined : num(movement?.unchanged),
    notComparable: isBaseline
      ? undefined
      : num(movement?.not_comparable ?? movement?.notComparable),
    n: isBaseline ? undefined : num(delta?.n),
    partial: isPartial,
    done,
    total,
  });
  const aggTooltip = [
    reducerTip,
    isBaseline ? "Baseline run" : null,
    isBaseline ? null : basisTip,
    summary,
    note?.tooltip ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Stack
      spacing={0.5}
      sx={{ width: "100%", minWidth: 0, py: 0.75 }}
      onClick={(e) => e.stopPropagation()}
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
        <Box sx={{ minWidth: 0, flex: 1, display: "flex" }}>
          {run && (
            <RunChip
              run={run}
              color={color}
              isBaseline={isBaseline}
              isPinned={!!run.is_dataset_baseline}
              showStatus={false}
              size="small"
            />
          )}
        </Box>
        {menuItems?.length > 0 && (
          <IconButton
            size="small"
            aria-label="Column menu"
            onClick={(e) => setAnchor(e.currentTarget)}
            sx={{ p: 0.25, color: "text.secondary" }}
          >
            <Iconify icon="mdi:dots-vertical" width={16} />
          </IconButton>
        )}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={basisLabel ? 0.75 : 1}
        sx={{ minWidth: 0, pl: 0.25 }}
      >
        {ruleLabel ? (
          <Typography
            component="span"
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="metric-header-rule"
            sx={{ flexShrink: 0 }}
          >
            {ruleLabel}
          </Typography>
        ) : null}
        <CustomTooltip
          show={!!aggTooltip}
          size="small"
          placement="top"
          arrow
          title={aggTooltip}
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color={isPartial ? "text.secondary" : "text.primary"}
            noWrap
            sx={{
              fontVariantNumeric: "tabular-nums",
              cursor: "default",
              // IT-R4 G3: the value never clips; the basis and the note
              // give way.
              flexShrink: basisLabel || note?.label ? 0 : undefined,
            }}
          >
            {formatValue(def, aggValue, { agg })}
          </Typography>
        </CustomTooltip>
        {ciText ? (
          <Typography
            component="span"
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="metric-header-ci"
            sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
          >
            {ciText}
          </Typography>
        ) : null}
        {note?.label && (
          <Typography
            component="span"
            typography="s3"
            noWrap
            data-testid="metric-header-note"
            title={note.tooltip ?? undefined}
            sx={{
              color: note.tone === "muted" ? "text.secondary" : "purple.600",
              minWidth: 0,
              flexShrink: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {note.label}
          </Typography>
        )}
        {note?.actions?.length > 0 && (
          <>
            <Link
              component="button"
              type="button"
              typography="s3"
              underline="hover"
              data-testid="metric-header-note-action"
              onClick={(e) => {
                e.stopPropagation();
                setNoteAnchor(e.currentTarget);
              }}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {note.actionLabel}
            </Link>
            <Menu
              anchorEl={noteAnchor}
              open={Boolean(noteAnchor)}
              onClose={() => setNoteAnchor(null)}
              onClick={(e) => e.stopPropagation()}
            >
              {note.actions.map((a) => (
                <MenuItem
                  key={a.label}
                  data-testid="metric-header-note-option"
                  sx={{ typography: "s2" }}
                  onClick={() => {
                    setNoteAnchor(null);
                    a.onClick();
                  }}
                >
                  {a.label}
                </MenuItem>
              ))}
            </Menu>
          </>
        )}
        {!note?.actions?.length && note?.onAction && (
          <Link
            component="button"
            type="button"
            typography="s3"
            underline="hover"
            data-testid="metric-header-note-action"
            onClick={(e) => {
              e.stopPropagation();
              note.onAction();
            }}
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            {note.actionLabel}
          </Link>
        )}
        {!isBaseline && delta && Number.isFinite(delta.delta) && (
          <DeltaBadge
            def={def}
            delta={delta.delta}
            ci95={num(delta.ci95)}
            significant={delta.significant}
            n={basisLabel ? undefined : num(delta.n)}
            vsLabel={delta.vsLabel}
            partial={isPartial}
          />
        )}
        {!isBaseline && basisLabel && delta && Number.isFinite(delta.delta) && (
          <CustomTooltip
            show={!!basisTip}
            size="small"
            placement="top"
            arrow
            title={basisTip ?? ""}
          >
            <Typography
              component="span"
              typography="s3"
              color="text.secondary"
              noWrap
              data-testid="metric-header-basis"
              sx={{
                cursor: "default",
                minWidth: 0,
                flexShrink: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {basisLabel}
            </Typography>
          </CustomTooltip>
        )}
        {!isBaseline && movement && (
          <MovementButtons
            improved={num(movement.improved) ?? 0}
            regressed={num(movement.regressed) ?? 0}
            unchanged={num(movement.unchanged)}
            notComparable={num(
              movement.not_comparable ?? movement.notComparable,
            )}
            active={movement.active ?? null}
            onSelect={onMovement}
          />
        )}
      </Stack>
      {menuItems?.length > 0 && (
        <Menu
          anchorEl={anchor}
          open={!!anchor}
          onClose={() => setAnchor(null)}
          slotProps={{ paper: { sx: { minWidth: 220 } } }}
        >
          {menuItems.map((item, i) => [
            item.divider && i > 0 ? <Divider key={`d-${i}`} /> : null,
            <MenuItem
              key={item.label}
              disabled={item.disabled}
              onClick={() => {
                setAnchor(null);
                item.onClick?.();
              }}
            >
              {item.icon && (
                <ListItemIcon sx={{ minWidth: 28 }}>
                  <Iconify icon={item.icon} width={16} />
                </ListItemIcon>
              )}
              <ListItemText
                primary={item.label}
                primaryTypographyProps={{ typography: "s1" }}
              />
            </MenuItem>,
          ])}
        </Menu>
      )}
    </Stack>
  );
};

MetricHeader.propTypes = {
  def: PropTypes.object,
  run: PropTypes.object,
  color: PropTypes.object,
  aggregate: PropTypes.oneOfType([PropTypes.number, PropTypes.object]),
  isBaseline: PropTypes.bool,
  partial: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
  ruleLabel: PropTypes.string,
  note: PropTypes.shape({
    label: PropTypes.string,
    tooltip: PropTypes.string,
    tone: PropTypes.oneOf(["na", "muted"]),
    actionLabel: PropTypes.string,
    onAction: PropTypes.func,
    actions: PropTypes.arrayOf(
      PropTypes.shape({
        label: PropTypes.string.isRequired,
        onClick: PropTypes.func.isRequired,
      }),
    ),
  }),
  movement: PropTypes.shape({
    improved: PropTypes.number,
    regressed: PropTypes.number,
    unchanged: PropTypes.number,
    not_comparable: PropTypes.number,
    notComparable: PropTypes.number,
    active: PropTypes.oneOf(["improved", "regressed", null]),
  }),
  delta: PropTypes.shape({
    delta: PropTypes.number,
    ci95: PropTypes.number,
    significant: PropTypes.bool,
    n: PropTypes.number,
    vsLabel: PropTypes.string,
  }),
  menuItems: PropTypes.arrayOf(PropTypes.object),
  onMovement: PropTypes.func,
  basis: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.shape({ label: PropTypes.string, tooltip: PropTypes.string }),
  ]),
};

export default MetricHeader;
