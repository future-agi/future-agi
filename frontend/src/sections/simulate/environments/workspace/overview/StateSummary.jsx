import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * StateSummary — the state-of-the-env answer that Overview leads with.
 *
 * Four tiles (Scenarios, Evaluations, Runs, Hard rules) each clickable to jump
 * to the relevant tab, plus a latest-run card when there is one. Every number
 * comes from the same state the Runs/Scenarios/Evaluations tabs read from, so
 * the summary and the detail can't drift.
 */
export default function StateSummary({ env, envState, counts, onGo }) {
  // A backed env passes real §6 counts; otherwise derive from the client store.
  // `??` (not `||`) so a real 0 is respected rather than falling back.
  const scenarioCount = counts?.scenarios ?? (envState?.scenarios?.length || 0);
  const evalCount = counts?.evaluations ?? (envState?.evals?.length || 0);
  const runs = envState?.runs || [];
  const runCount = counts?.runs ?? runs.length;
  const ruleCount = counts?.hardRules ?? (env?.rules?.length || 0);
  const latest = runs[0] || null;
  const latestPassRate = latest?.passRate != null ? Math.round(latest.passRate) : null;

  const tiles = [
    { id: "scenarios", label: "Scenarios", value: scenarioCount, icon: "solar:layers-minimalistic-linear", to: "scenarios" },
    { id: "evals", label: "Evaluations", value: evalCount, icon: "solar:shield-check-linear", to: "evals" },
    { id: "runs", label: "Runs", value: runCount, icon: "solar:play-circle-linear", to: "runs", disabled: !runCount },
    { id: "rules", label: "Hard rules", value: ruleCount, icon: "solar:shield-keyhole-linear", to: "contract" },
  ];

  return (
    <Box sx={{ mb: 3 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ mb: latest ? 1.5 : 0 }}>
        {tiles.map((t) => (
          <Box
            key={t.id}
            onClick={t.disabled ? undefined : () => onGo?.(t.to)}
            sx={{
              flex: 1, minWidth: 0, p: 1.75, borderRadius: 1.5,
              border: "1px solid", borderColor: "divider",
              bgcolor: "background.paper",
              cursor: t.disabled ? "default" : "pointer",
              transition: "border-color .12s ease, background-color .12s ease",
              "&:hover": t.disabled ? {} : {
                borderColor: "text.disabled",
                bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.03 : 0.02),
              },
            }}
          >
            <Stack direction="row" alignItems="center" spacing={1}>
              <Iconify icon={t.icon} width={14} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4, flex: 1 }}>
                {t.label}
              </Typography>
              {!t.disabled && (
                <Iconify icon="solar:alt-arrow-right-linear" width={12} sx={{ color: "text.disabled" }} />
              )}
            </Stack>
            <Typography sx={{ typography: "h6", fontWeight: 700, mt: 0.5, fontVariantNumeric: "tabular-nums" }}>
              {t.value}
            </Typography>
          </Box>
        ))}
      </Stack>

      {latest && (
        <Box
          onClick={() => onGo?.("runs")}
          sx={{
            p: 1.75, borderRadius: 1.5, cursor: "pointer",
            border: "1px solid", borderColor: "divider",
            bgcolor: "background.paper",
            "&:hover": {
              borderColor: "text.disabled",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.02),
            },
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Box
              sx={{
                width: 32, height: 32, borderRadius: 1, display: "grid", placeItems: "center",
                flexShrink: 0,
                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                color: "#16A34A",
              }}
            >
              <Iconify icon="solar:play-circle-linear" width={16} />
            </Box>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4 }}>
                Latest run
              </Typography>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>
                {latest.label || latest.name || "Run"}
                {latest.agentVersion ? ` · ${latest.agentVersion}` : ""}
              </Typography>
            </Box>
            {latestPassRate != null && (
              <Stack alignItems="flex-end" sx={{ flexShrink: 0 }}>
                <Typography sx={{ typography: "s1", fontWeight: 700, color: latestPassRate >= 80 ? "#16A34A" : latestPassRate >= 50 ? "#CA8A04" : "#DC2626", fontVariantNumeric: "tabular-nums" }}>
                  {latestPassRate}%
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  pass rate
                </Typography>
              </Stack>
            )}
            <Iconify icon="solar:alt-arrow-right-linear" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
          </Stack>
        </Box>
      )}
    </Box>
  );
}

StateSummary.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  counts: PropTypes.shape({
    scenarios: PropTypes.number,
    evaluations: PropTypes.number,
    runs: PropTypes.number,
    hardRules: PropTypes.number,
  }),
  onGo: PropTypes.func,
};
