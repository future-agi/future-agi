import MoneyValue from "src/sections/common/format/MoneyValue";
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  ListItemIcon,
  MenuItem,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

// Column stats popover (area-datasets G5): opened from a Data grid header's
// stats line. Plain MUI boxes, no chart library: a stacked pass/fail/error
// bar, a 10-bin histogram for scores, top values for metadata, empties for
// inputs and references, latency/cost for generated outputs.

const pct = (x) => `${Math.round((x ?? 0) * 100)}%`;
const n2 = (x) => (x == null ? "—" : (Math.round(x * 100) / 100).toFixed(2));

const KIND_LABEL = {
  pass_fail: "Pass / fail eval",
  score: "Score eval",
  choices: "Choices eval",
  output: "Generated output",
  expected: "Expected output",
  input: "Input",
  metadata: "Metadata",
};

const Stat = ({ label, value, tone }) => (
  <Box sx={{ minWidth: 0 }}>
    <Typography typography="s3" color="text.secondary" noWrap>
      {label}
    </Typography>
    <Typography
      typography="m3"
      fontWeight="fontWeightSemiBold"
      color={tone ? `${tone}.main` : "text.primary"}
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {value}
    </Typography>
  </Box>
);
Stat.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  tone: PropTypes.string,
};

const StackedBar = ({ segments, total }) => (
  <Box
    sx={{
      display: "flex",
      height: 8,
      borderRadius: 4,
      overflow: "hidden",
      bgcolor: "action.hover",
    }}
  >
    {segments
      .filter((s) => s.count > 0)
      .map((s) => (
        <Box
          key={s.label}
          title={`${s.label} · ${s.count}`}
          sx={{
            width: `${(s.count / Math.max(1, total)) * 100}%`,
            bgcolor: s.color,
          }}
        />
      ))}
  </Box>
);
StackedBar.propTypes = { segments: PropTypes.array, total: PropTypes.number };

const Histogram = ({ bins, threshold }) => {
  const max = Math.max(1, ...bins.map((b) => b.count));
  return (
    <Box>
      <Box
        sx={{ display: "flex", alignItems: "flex-end", gap: "3px", height: 64 }}
      >
        {bins.map((b) => {
          const below = threshold != null && b.x1 <= threshold;
          return (
            <Box
              key={b.x0}
              title={`${n2(b.x0)}–${n2(b.x1)} · ${b.count} rows`}
              sx={{
                flex: 1,
                height: `${Math.max(2, (b.count / max) * 64)}px`,
                borderRadius: "2px 2px 0 0",
                bgcolor: below ? "error.light" : "primary.light",
                opacity: b.count ? 1 : 0.35,
              }}
            />
          );
        })}
      </Box>
      <Stack direction="row" justifyContent="space-between" sx={{ mt: 0.5 }}>
        <Typography typography="s3" color="text.disabled">
          {n2(bins[0]?.x0)}
        </Typography>
        {threshold != null && (
          <Typography typography="s3" color="error.main">
            Pass threshold {threshold}
          </Typography>
        )}
        <Typography typography="s3" color="text.disabled">
          {n2(bins.at(-1)?.x1)}
        </Typography>
      </Stack>
    </Box>
  );
};
Histogram.propTypes = { bins: PropTypes.array, threshold: PropTypes.number };

function body(stats) {
  const total = stats.total ?? 0;
  switch (stats.kind) {
    case "pass_fail": {
      const fail = stats.fail_count ?? 0;
      const err = stats.error_count ?? 0;
      const pass = Math.max(0, (stats.completed ?? total) - fail);
      return (
        <Stack gap={1.5}>
          <Stack direction="row" gap={3}>
            <Stat label="Pass rate" value={pct(stats.pass_rate)} />
            <Stat
              label="Failed"
              value={fail}
              tone={fail ? "error" : undefined}
            />
            <Stat
              label="Errors"
              value={err}
              tone={err ? "warning" : undefined}
            />
          </Stack>
          <StackedBar
            total={total}
            segments={[
              { label: "Pass", count: pass, color: "success.main" },
              { label: "Fail", count: fail, color: "error.main" },
              { label: "Error", count: err, color: "warning.main" },
            ]}
          />
          <Typography typography="s3" color="text.secondary">
            {stats.completed ?? total} of {total} rows scored
          </Typography>
        </Stack>
      );
    }
    case "score":
      return (
        <Stack gap={1.5}>
          <Stack direction="row" gap={3}>
            <Stat label="Average" value={n2(stats.avg)} />
            <Stat label="Median" value={n2(stats.p50)} />
            {stats.threshold != null && (
              <Stat
                label={`Below ${stats.threshold}`}
                value={stats.below_threshold ?? 0}
                tone={stats.below_threshold ? "error" : undefined}
              />
            )}
          </Stack>
          {stats.histogram?.length ? (
            <Histogram bins={stats.histogram} threshold={stats.threshold} />
          ) : null}
          <Typography typography="s3" color="text.secondary">
            {stats.completed ?? total} of {total} rows scored
            {stats.error_count ? ` · ${stats.error_count} errors` : ""}
          </Typography>
        </Stack>
      );
    case "output":
      return (
        <Stack gap={1.5}>
          <Stack direction="row" gap={3}>
            <Stat
              label="p50 latency"
              value={
                stats.latency_p50_ms != null
                  ? `${(stats.latency_p50_ms / 1000).toFixed(2)} s`
                  : "—"
              }
            />
            <Stat
              label="Total cost"
              value={<MoneyValue value={stats.cost_total} aggregate />}
            />
            <Stat
              label="Errors"
              value={stats.error_count ?? 0}
              tone={stats.error_count ? "error" : undefined}
            />
          </Stack>
          <StackedBar
            total={total}
            segments={[
              {
                label: "Completed",
                count: (stats.completed ?? 0) - (stats.error_count ?? 0),
                color: "info.main",
              },
              {
                label: "Errors",
                count: stats.error_count ?? 0,
                color: "error.main",
              },
            ]}
          />
          <Typography typography="s3" color="text.secondary">
            {stats.completed ?? 0} of {total} rows generated
          </Typography>
        </Stack>
      );
    default: {
      const empty = stats.empty_count ?? 0;
      const top = (stats.top_choices ?? []).filter((c) => c.count > 1);
      return (
        <Stack gap={1.5}>
          <Stack direction="row" gap={3}>
            <Stat label="Filled" value={`${total - empty} of ${total}`} />
            <Stat
              label="Empty"
              value={empty}
              tone={empty && stats.kind === "expected" ? "warning" : undefined}
            />
          </Stack>
          <StackedBar
            total={total}
            segments={[
              { label: "Filled", count: total - empty, color: "primary.light" },
              { label: "Empty", count: empty, color: "warning.light" },
            ]}
          />
          {top.length > 0 && (
            <Stack gap={0.75}>
              <Typography typography="s3" color="text.secondary">
                Top values
              </Typography>
              {top.slice(0, 6).map((c) => (
                <Stack
                  key={c.value}
                  direction="row"
                  alignItems="center"
                  gap={1}
                >
                  <Typography
                    typography="s2"
                    noWrap
                    sx={{ width: 120, flexShrink: 0 }}
                  >
                    {c.value}
                  </Typography>
                  <Box
                    sx={{
                      flex: 1,
                      height: 6,
                      borderRadius: 3,
                      bgcolor: "action.hover",
                    }}
                  >
                    <Box
                      sx={{
                        width: `${(c.count / Math.max(1, total)) * 100}%`,
                        height: "100%",
                        borderRadius: 3,
                        bgcolor: "primary.light",
                      }}
                    />
                  </Box>
                  <Typography
                    typography="s3"
                    color="text.secondary"
                    sx={{
                      width: 28,
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {c.count}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          )}
        </Stack>
      );
    }
  }
}

/** Actions offered per kind, in menu order. */
export function statsActions(stats, { canHide = true } = {}) {
  if (!stats) return [];
  const out = [];
  if (stats.kind === "pass_fail" && stats.fail_count)
    out.push({
      key: "failed",
      label: "Show failed rows",
      icon: "mdi:close-circle-outline",
    });
  if (
    stats.kind === "score" &&
    stats.threshold != null &&
    stats.below_threshold
  )
    out.push({
      key: "below",
      label: `Show rows below ${stats.threshold}`,
      icon: "mdi:arrow-collapse-down",
    });
  if (stats.error_count)
    out.push({
      key: "errors",
      label: "Show errored rows",
      icon: "mdi:alert-circle-outline",
    });
  if (
    ["expected", "input", "metadata"].includes(stats.kind) &&
    stats.empty_count
  )
    out.push({
      key: "empty",
      label: "Show empty rows",
      icon: "mdi:checkbox-blank-outline",
    });
  if (["pass_fail", "score"].includes(stats.kind)) {
    out.push({
      key: "sortWorst",
      label: "Sort worst first",
      icon: "mdi:sort-ascending",
    });
    out.push({
      key: "sortBest",
      label: "Sort best first",
      icon: "mdi:sort-descending",
    });
  }
  if (canHide)
    out.push({
      key: "hide",
      label: "Hide column",
      icon: "mdi:eye-off-outline",
    });
  return out;
}

const ColumnStatsPopover = ({ anchorEl, onClose, column, onAction }) => {
  const stats = column?.stats;
  const open = Boolean(anchorEl && stats);
  const actions = statsActions(stats);
  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: -4, horizontal: "left" }}
      onClick={(e) => e.stopPropagation()}
      PaperProps={{ sx: { width: 320, p: 0 } }}
    >
      {stats && (
        <>
          <Box sx={{ px: 2, pt: 1.5, pb: 1 }}>
            <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
              {column.name}
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {KIND_LABEL[stats.kind] ?? "Column"} · {stats.total ?? 0} rows
            </Typography>
          </Box>
          <Box sx={{ px: 2, pb: 1.5 }}>{body(stats)}</Box>
          {actions.length > 0 && (
            <>
              <Divider />
              <Box sx={{ py: 0.5 }}>
                {actions.map((a) => (
                  <MenuItem
                    key={a.key}
                    dense
                    onClick={() => {
                      onAction?.(a.key);
                      onClose?.();
                    }}
                    sx={{ typography: "s2", py: 0.75 }}
                  >
                    <ListItemIcon sx={{ minWidth: 28 }}>
                      <Iconify icon={a.icon} width={16} />
                    </ListItemIcon>
                    {a.label}
                  </MenuItem>
                ))}
              </Box>
            </>
          )}
        </>
      )}
    </Popover>
  );
};

ColumnStatsPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  column: PropTypes.object,
  onAction: PropTypes.func,
};

export default ColumnStatsPopover;
