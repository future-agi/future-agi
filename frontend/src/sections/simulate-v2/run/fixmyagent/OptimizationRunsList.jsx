import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { EmptyState } from "../../components/primitives";
import { optimizationSummary, optimizationVerdict, OPTIMIZER_MODELS } from "../../_mock/optimizationRuns";

/**
 * Past optimization runs.
 *
 * The column that leads is the held-out score, never the training best. A list
 * ranked by what each run scored on the split it was tuned against is a
 * leaderboard of overfitting, and it is exactly the number a team would quote
 * in a review if it were the one on the left.
 *
 * Runs that produced a gamed winner or broke a release blocker are marked here
 * rather than only inside the detail view — the whole point of checking is that
 * the check is visible from the place people skim.
 */

const dateOf = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, "0")} ${d.toLocaleString("en", { month: "short" })}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

export default function OptimizationRunsList({ records = [], onOpen, onNew, scope, emptyBody }) {
  if (!records.length) {
    return (
      <EmptyState
        icon="solar:magic-stick-3-linear"
        title="No optimization runs yet"
        body={emptyBody || "Open Fix my agent, pick the changes worth trying, and start a search."}
        action={onNew ? <Button variant="contained" color="primary" onClick={onNew} sx={{ typography: "s2", fontWeight: 700 }}>Fix my agent</Button> : undefined}
      />
    );
  }

  return (
    <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
      {records.map((r) => {
        const s = optimizationSummary(r);
        const verdict = optimizationVerdict(r);
        const flagged = verdict && verdict.tone !== "#16A34A";
        const model = OPTIMIZER_MODELS.find((m) => m.id === r.model);
        return (
          <Stack
            key={r.id}
            direction="row" alignItems="center" spacing={2}
            onClick={() => onOpen?.(r)}
            sx={{ px: 2.5, py: 1.75, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
          >
            <Box flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={0.875} flexWrap="wrap" rowGap={0.5}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{r.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.disabled" }}>{r.id}</Typography>
                {r.status === "running" && (
                  <Chip
                    size="small" label="Running"
                    sx={{
                      height: 18, borderRadius: 0.5, color: "#CA8A04",
                      border: "1px solid", borderColor: alpha("#CA8A04", 0.4), bgcolor: "transparent",
                      "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                    }}
                  />
                )}
                {flagged && (
                  <Tooltip arrow title={verdict.title}>
                    <Box component="span" sx={{ display: "flex" }}>
                      <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: verdict.tone }} />
                    </Box>
                  </Tooltip>
                )}
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {r.result?.optimizer?.label} · {model?.label || r.model} · {r.result?.trials?.length || 0} trials
                {scope === "environment" && r.fromRunId ? " · from a completed run" : ""}
              </Typography>
            </Box>

            <Box sx={{ textAlign: "right", flexShrink: 0 }}>
              <Typography sx={{ typography: "s1", fontWeight: 700, color: s.tone, fontVariantNumeric: "tabular-nums" }}>
                {s.headline}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{s.sub}</Typography>
            </Box>

            <Typography
              sx={{ typography: "s3", color: "text.disabled", width: 96, textAlign: "right", flexShrink: 0 }}
            >
              {dateOf(r.createdAt)}
            </Typography>

            <Iconify icon="eva:arrow-ios-forward-fill" width={15} sx={{ color: "text.disabled", flexShrink: 0 }} />
          </Stack>
        );
      })}
    </Stack>
  );
}

OptimizationRunsList.propTypes = {
  records: PropTypes.array,
  onOpen: PropTypes.func,
  onNew: PropTypes.func,
  scope: PropTypes.string,
  emptyBody: PropTypes.string,
};
