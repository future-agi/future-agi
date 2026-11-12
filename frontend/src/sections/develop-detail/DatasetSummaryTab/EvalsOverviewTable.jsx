import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { NUM_SX } from "src/sections/common/format/fmt";
import { HeaderMenuCell } from "src/sections/common/ux/oneview/HeaderMenu";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import {
  DS_EVALS_COLUMNS,
  DS_EVALS_TIPS as TIPS,
  dsEvalsMinWidth,
  useDsEvalsLayout,
} from "./dsEvalsLayout";
import { useTheme } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import MiniHistogram from "src/sections/common/Analysis/MiniHistogram";
import { datasetGridHref } from "src/sections/common/Analysis/analysisLinks";
import {
  bestAgreementFor,
  failingText,
  kappaBand,
  kappaText,
  num,
  pct,
  sortEvalRows,
  toMiniHistogram,
} from "./summaryApi";

const tabular = NUM_SX;

/** Pass rate as a bar with the number (AS#47). */
function PassRateBar({ value }) {
  if (value == null || !Number.isFinite(Number(value)))
    return (
      <Typography typography="s2" color="text.disabled">
        {pct(value)}
      </Typography>
    );
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      justifyContent="flex-end"
    >
      <Box
        sx={{
          width: 56,
          height: 6,
          borderRadius: 3,
          bgcolor: "action.hover",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            width: `${Math.max(0, Math.min(1, value)) * 100}%`,
            height: "100%",
            bgcolor: "green.500",
          }}
        />
      </Box>
      <Typography typography="s2" sx={{ ...tabular, minWidth: 44 }}>
        {pct(value)}
      </Typography>
    </Stack>
  );
}
PassRateBar.propTypes = { value: PropTypes.number };

const KAPPA_TONE = {
  good: "success",
  fair: "warning",
  poor: "error",
};

/** `v3`, or amber `v2 → v3 available` when a newer version exists. */
export function EvalVersion({ version, latestVersion }) {
  if (!version) return <Typography typography="s2">—</Typography>;
  const newer = latestVersion && latestVersion !== version;
  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Box
        component="span"
        sx={{
          typography: "s3",
          px: 0.5,
          borderRadius: 0.5,
          bgcolor: "action.hover",
          color: "text.secondary",
          ...tabular,
        }}
      >
        {version}
      </Box>
      {newer && (
        <Typography typography="s3" sx={{ color: "warning.dark" }}>
          {latestVersion} available
        </Typography>
      )}
    </Stack>
  );
}

EvalVersion.propTypes = {
  version: PropTypes.string,
  latestVersion: PropTypes.string,
};

/** Pass / Fail / Error as one stacked bar; counts in the tooltip. */
export function PassFailBar({ bins }) {
  const theme = useTheme();
  const tone = {
    Pass: theme.palette.green[500],
    Fail: theme.palette.red[500],
    Error: theme.palette.grey[400],
  };
  const total = (bins ?? []).reduce((s, b) => s + (b.count ?? 0), 0);
  if (!total) return <Typography typography="s2">—</Typography>;
  const text = bins.map((b) => `${b.label} ${b.count}`).join(" · ");
  return (
    <CustomTooltip show size="small" arrow title={text}>
      <Stack
        direction="row"
        aria-label={text}
        sx={{ height: 10, gap: "2px", borderRadius: "2px", overflow: "hidden" }}
      >
        {bins
          .filter((b) => b.count > 0)
          .map((b) => (
            <Box
              key={b.label}
              sx={{ flex: b.count, bgcolor: tone[b.label] ?? tone.Error }}
            />
          ))}
      </Stack>
    </CustomTooltip>
  );
}

PassFailBar.propTypes = { bins: PropTypes.array };

export const directionText = (higherIsBetter) =>
  higherIsBetter === false ? "Lower is better" : "Higher is better";

/**
 * EvalsOverviewTable — one scannable row per eval on the dataset: version,
 * direction, pass rate, mean, distribution, scored, errors, judge vs human,
 * and the failing rows (opens the Data tab with exactly those rows).
 *
 * One view (A1-37): the columns are the user's layout (`dsEvals`, per
 * dataset). Default = Eval · Pass rate · Mean · Distribution · Scored ·
 * Errors · Failing rows; Version · Dir. · Judge vs human are one click away
 * (`3 hidden`, in the card header). Direction is never lost: the Pass rate
 * header's HoverCard names each eval's direction. Right-click a header (or
 * its ⋮, or Shift+F10) for the Excel menu.
 */
const EvalsOverviewTable = ({ rows, agreement, datasetId, version }) => {
  const theme = useTheme();
  const layout = useDsEvalsLayout(datasetId, { commands: false });
  const colors = { rows: { solid: theme.palette.primary.main } };
  const [sort, setSort] = useState({ key: "passRate", dir: "asc" });
  const sorted = useMemo(
    () =>
      sortEvalRows(rows, sort.key, sort.dir, (r) =>
        bestAgreementFor(agreement, r.id),
      ),
    [rows, sort, agreement],
  );
  const menuSort = { id: sort.key, desc: sort.dir === "desc" };
  const onMenuSort = (next) =>
    setSort(
      next
        ? { key: next.id, dir: next.desc ? "desc" : "asc" }
        : { key: "passRate", dir: "asc" },
    );
  const visible = layout.visibleIds;
  const byId = Object.fromEntries(DS_EVALS_COLUMNS.map((c) => [c.id, c]));

  const passRateHelp = (
    <HoverCard
      trigger={<span>Pass rate</span>}
      title="Pass rate"
      stats={(rows ?? []).map((r) => ({
        label: r.name,
        value: directionText(r.higherIsBetter),
      }))}
      triggerProps={{ onClick: undefined }}
    >
      {TIPS.passRate}
    </HoverCard>
  );

  const head = (id, i) => {
    const c = byId[id];
    const active = sort.key === id;
    const text = id === "passRate" ? passRateHelp : c.label;
    const label = c.sortable ? (
      <TableSortLabel
        active={active}
        direction={active ? sort.dir : "asc"}
        onClick={() =>
          setSort((p) => ({
            key: id,
            dir: p.key === id && p.dir === "asc" ? "desc" : "asc",
          }))
        }
      >
        {text}
      </TableSortLabel>
    ) : (
      text
    );
    return (
      <HeaderMenuCell
        key={id}
        column={id}
        layout={layout}
        sort={menuSort}
        onSort={onMenuSort}
        align={c.align ?? "left"}
        sortDirection={active ? sort.dir : false}
        sx={{ fontWeight: 600, ...(i === 0 ? { pl: 1.5 } : {}) }}
      >
        {c.description ? (
          <CustomTooltip
            show
            size="small"
            placement="top"
            title={c.description}
          >
            <span>{label}</span>
          </CustomTooltip>
        ) : (
          label
        )}
      </HeaderMenuCell>
    );
  };

  const cell = (id, row, pair, href) => {
    switch (id) {
      case "name":
        return (
          <TableCell key={id} sx={{ pl: 1.5 }}>
            <Typography typography="s2" fontWeight={500}>
              {row.name}
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {row.passFail ? "Pass/Fail" : "Score"} · {row.templateName}
            </Typography>
          </TableCell>
        );
      case "version":
        return (
          <TableCell key={id}>
            <EvalVersion
              version={row.version}
              latestVersion={row.latestVersion}
            />
          </TableCell>
        );
      case "direction":
        return (
          <TableCell key={id}>
            <CustomTooltip
              show
              size="small"
              placement="top"
              title={directionText(row.higherIsBetter)}
            >
              <Box
                component="span"
                aria-label={directionText(row.higherIsBetter)}
                sx={{ display: "inline-flex" }}
              >
                <DirectionIcon higherIsBetter={row.higherIsBetter ?? true} />
              </Box>
            </CustomTooltip>
          </TableCell>
        );
      case "passRate":
        return (
          <TableCell key={id} align="right">
            <PassRateBar value={row.passRate} />
          </TableCell>
        );
      case "mean":
        return (
          <TableCell key={id} align="right" sx={tabular}>
            {row.passFail ? (
              <CustomTooltip
                show
                size="small"
                placement="top"
                title="Not applicable: Pass/Fail eval (see the pass rate)"
              >
                <Box component="span" sx={{ color: "text.disabled" }}>
                  {num(null)}
                </Box>
              </CustomTooltip>
            ) : (
              num(row.mean)
            )}
          </TableCell>
        );
      case "dist":
        return (
          <TableCell key={id} sx={{ width: 112, py: 0.5 }}>
            <Box sx={{ width: 100 }}>
              {row.passFail ? (
                <PassFailBar bins={row.histogram} />
              ) : (
                <MiniHistogram
                  histogram={toMiniHistogram(row.histogram, row.passFail)}
                  runs={[
                    { run_id: "rows", label: row.passFail ? "" : row.name },
                  ]}
                  colors={colors}
                  threshold={row.passThreshold ?? undefined}
                  height={22}
                  formatX={(v) => Number(v).toFixed(1)}
                />
              )}
            </Box>
          </TableCell>
        );
      case "scored":
        return (
          <TableCell key={id} align="right" sx={tabular}>
            {row.scored ?? "—"}
            {row.totalRows != null && (
              <Typography
                component="span"
                typography="s3"
                color="text.secondary"
              >
                {" "}
                / {row.totalRows}
              </Typography>
            )}
          </TableCell>
        );
      case "errors":
        return (
          <TableCell
            key={id}
            align="right"
            sx={{
              ...tabular,
              color: row.errors ? "red.600" : "text.secondary",
            }}
          >
            {row.errors ?? 0}
          </TableCell>
        );
      case "agreement":
        return (
          <TableCell key={id}>
            {pair ? (
              <CustomTooltip
                show
                size="small"
                arrow
                title={`Agrees with "${pair.label_name}" on ${pct(pair.agreement_pct, 0)} of ${pair.n} rows. ${kappaText(pair.kappa, pair.n)}`}
              >
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <Typography typography="s2" sx={tabular}>
                    {pct(pair.agreement_pct, 0)}
                  </Typography>
                  {kappaBand(pair.kappa) && (
                    <Box
                      component="span"
                      sx={(t) => {
                        const c =
                          t.palette[KAPPA_TONE[kappaBand(pair.kappa).tone]];
                        return {
                          typography: "s3",
                          px: 0.5,
                          borderRadius: 0.5,
                          color: t.palette.mode === "dark" ? c.light : c.dark,
                          bgcolor: alpha(c.main, 0.12),
                          ...tabular,
                        };
                      }}
                    >
                      κ {num(pair.kappa)}
                    </Box>
                  )}
                </Stack>
              </CustomTooltip>
            ) : (
              <Typography typography="s2" color="text.disabled">
                —
              </Typography>
            )}
          </TableCell>
        );
      case "failing":
        return (
          <TableCell key={id}>
            {row.failing ? (
              <Stack direction="row" alignItems="center" gap={1}>
                <Typography typography="s2" sx={tabular}>
                  {failingText(row)}
                </Typography>
                <Link
                  component={RouterLink}
                  to={href}
                  underline="hover"
                  typography="s2"
                  fontWeight={500}
                >
                  View rows
                </Link>
              </Stack>
            ) : (
              <Typography typography="s2" color="text.secondary">
                None
              </Typography>
            )}
          </TableCell>
        );
      default:
        return null;
    }
  };

  return (
    <Box sx={{ overflowX: "auto" }} data-qa-clip>
      <Table
        size="small"
        sx={{
          minWidth: dsEvalsMinWidth(visible),
          "& td, & th": {
            typography: "s2",
            borderColor: "divider",
            whiteSpace: "nowrap",
            px: 1,
          },
        }}
      >
        <TableHead>
          <TableRow sx={{ bgcolor: "background.neutral" }}>
            {visible.map(head)}
          </TableRow>
        </TableHead>
        <TableBody>
          {sorted.map((row) => {
            const pair = bestAgreementFor(agreement, row.id);
            const href = datasetGridHref(datasetId, row.failingFilters, {
              version,
            });
            return (
              <TableRow key={row.id} hover>
                {visible.map((id) => cell(id, row, pair, href))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Box>
  );
};

EvalsOverviewTable.propTypes = {
  rows: PropTypes.array.isRequired,
  agreement: PropTypes.array,
  datasetId: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default EvalsOverviewTable;
