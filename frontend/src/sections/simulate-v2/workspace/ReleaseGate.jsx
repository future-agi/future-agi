import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Slider, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { defaultGate, gateVerdict, systemMetrics } from "../_mock/grading";
import { SectionCard } from "../components/primitives";

/**
 * Can this version ship?
 *
 * A run that reports 82% does not answer that. A gate does: named thresholds,
 * every one of them sourced from an eval or a system metric the platform
 * already produces, and one verdict at the top that a release process can key
 * off. The rules are the interesting part — a team that never writes them down
 * ends up arguing about the number instead.
 */
/** "$0.50" reads; "0.5$" does not. */
const amount = (n, unit) => (unit === "$" ? `$${n}` : `${n}${unit}`);

export default function ReleaseGate({ envState, patch }) {
  const rules = envState.gate || defaultGate();
  const verdict = gateVerdict(rules);
  const metrics = systemMetrics();

  const setTarget = (id, target) =>
    patch({ gate: rules.map((r) => (r.id === id ? { ...r, target } : r)) });

  return (
    <SectionCard
      title="Release gate"
      subtitle="What has to be true before this agent version can ship"
      action={
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{
            px: 1, py: 0.5, borderRadius: 0.875,
            bgcolor: (t) => alpha(verdict.passed ? "#16A34A" : "#DC2626", t.palette.mode === "dark" ? 0.16 : 0.1),
          }}
        >
          <Iconify
            icon={verdict.passed ? "solar:check-circle-bold" : "solar:close-circle-bold"}
            width={14}
            sx={{ color: verdict.passed ? "#16A34A" : "#DC2626" }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: verdict.passed ? "#16A34A" : "#DC2626" }}>
            {verdict.passed ? "Cleared to ship" : `Blocked — ${verdict.failed.length} not met`}
          </Typography>
        </Stack>
      }
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {rules.map((r) => {
          const failed = r.ceiling ? r.actual > r.target : r.actual < r.target;
          return (
            <Stack key={r.id} direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 1.5 }}>
              <Iconify
                icon={failed ? "solar:close-circle-bold" : "solar:check-circle-bold"}
                width={15}
                sx={{ color: failed ? "#DC2626" : "#16A34A", flexShrink: 0 }}
              />
              <Box sx={{ width: 190, flexShrink: 0, minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.label}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {r.ceiling ? "must stay under" : "must reach"} {amount(r.target, r.unit)}
                </Typography>
              </Box>

              <Box sx={{ flex: 1, minWidth: 120, display: { xs: "none", md: "block" } }}>
                <Slider
                  size="small"
                  value={r.target}
                  min={0}
                  max={r.unit === "%" ? 100 : r.unit === "s" ? 10 : 2}
                  step={r.unit === "%" ? 1 : 0.1}
                  onChange={(_, v) => setTarget(r.id, v)}
                  sx={{ py: 1, "& .MuiSlider-thumb": { width: 11, height: 11 } }}
                />
              </Box>

              <Typography
                sx={{
                  width: 96, textAlign: "right", flexShrink: 0,
                  typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                  color: failed ? "#DC2626" : "text.primary",
                }}
              >
                {amount(r.actual, r.unit)} now
              </Typography>
            </Stack>
          );
        })}
      </Stack>

      <Stack
        direction="row"
        spacing={2}
        sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral", flexWrap: "wrap", rowGap: 1 }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, width: "100%" }}>
          System metrics — observed, available to gate on
        </Typography>
        {metrics.map((m) => (
          <Tooltip key={m.id} arrow title={m.note}>
            <Stack direction="row" alignItems="baseline" spacing={0.75}>
              <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{m.value}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{m.label}</Typography>
            </Stack>
          </Tooltip>
        ))}
      </Stack>
    </SectionCard>
  );
}

ReleaseGate.propTypes = { envState: PropTypes.object.isRequired, patch: PropTypes.func.isRequired };
