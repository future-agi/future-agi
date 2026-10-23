import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Box, Button, Dialog, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useOptionalSimStore } from "../store";
import { uniqueEnvName } from "../_mock/envNames";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";

const MONO = "ui-monospace, Menlo, monospace";
/* Quick picks; any count from 1 to 1,000 can be typed — the range the build accepts. */
const SIZES = [10, 25, 50, 100];
const MIN_SCENARIOS = 1;
const MAX_SCENARIOS = 1000;

/**
 * "Build environment" from an agent: the three choices the builder can't
 * make for you — what to call it, which version of the agent it's built
 * from (and tests first), and how many scenarios to start with.
 */
export default function BuildEnvironmentDialog({ open, onClose, agent, defaultVersion, onBuild, channel = null }) {
  const [name, setName] = useState("");
  const envs = useOptionalSimStore()?.state?.myEnvironments || [];
  const [version, setVersion] = useState(defaultVersion);
  /* Held as typed, so a half-typed number isn't snapped mid-edit. */
  const [countText, setCountText] = useState("10");
  const typed = Math.floor(Number(countText));
  const countOk = Number.isFinite(typed) && typed >= MIN_SCENARIOS && typed <= MAX_SCENARIOS;
  const count = countOk ? typed : Math.min(MAX_SCENARIOS, Math.max(MIN_SCENARIOS, Number.isFinite(typed) ? typed : MIN_SCENARIOS));

  useEffect(() => {
    if (!open) return;
    /* Numbered if taken — the name it will have, not one the store changes later. */
    setName(uniqueEnvName(`${agent.name.replace(/\s+agent$/i, "")} environment`, envs));
    /* A draft is never what an environment tests — start from production. */
    const pick = agent.versions.find((v) => v.label === (defaultVersion || agent.latest));
    setVersion(pick && !pick.pendingDraft ? pick.label : agent.production || agent.latest);
    setCountText("10");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Dialog
      open={open} onClose={onClose}
      PaperProps={{ sx: { borderRadius: 2, maxWidth: 480, width: "100%", bgcolor: "background.paper", backgroundImage: "none", border: "1px solid", borderColor: "divider" } }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 1 }}>
        <Typography sx={{ typography: "m3", fontWeight: 700 }}>{channel ? `Build a ${channel} environment` : "Build environment"}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {`We read ${agent.name} and build its environment — simulated tools, seeded data, rules and scenarios.`}
        </Typography>
      </Box>
      <Stack spacing={2} sx={{ px: 3, py: 2 }}>
        <Box>
          <Label>Name</Label>
          <TextField size="small" fullWidth value={name} onChange={(e) => setName(e.target.value)} InputProps={{ sx: { typography: "s2" } }} />
        </Box>
        <Stack direction="row" spacing={1.5}>
          <Box flex={1}>
            <Label>{isGitAgent(agent) ? "Built from commit" : "Built from version"}</Label>
            <Select size="small" fullWidth value={version} onChange={(e) => setVersion(e.target.value)} sx={{ typography: "s2" }}>
              {[...agent.versions].reverse().map((v) => (
                <MenuItem key={v.label} value={v.label} disabled={!!v.pendingDraft} sx={{ typography: "s2" }}>
                  <Box component="span" sx={{ fontFamily: MONO, fontWeight: 600, mr: 1 }}>{versionNameOf(agent, v.label).main}</Box>
                  <Box component="span" sx={{ color: "text.subtitle" }}>
                    {v.pendingDraft ? (isGitAgent(agent) ? "fix branch — merge it first" : "draft — release it first") : v.label === agent.production ? "in production" : ""}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </Box>
          <Box flex={1}>
            <Label>Scenarios to start with</Label>
            <TextField
              size="small" fullWidth type="number"
              value={countText}
              placeholder="10"
              onChange={(e) => setCountText(e.target.value)}
              onBlur={() => setCountText(String(count))}
              error={!countOk}
              inputProps={{ min: MIN_SCENARIOS, max: MAX_SCENARIOS, step: 1, "aria-label": "Scenarios to start with" }}
              InputProps={{ sx: { typography: "s2", fontVariantNumeric: "tabular-nums" } }}
            />
          </Box>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: -1 }}>
          {SIZES.map((n) => {
            const on = countOk && typed === n;
            return (
              <Box
                key={n}
                component="button"
                type="button"
                aria-pressed={on}
                onClick={() => setCountText(String(n))}
                sx={{
                  px: 1, height: 22, borderRadius: 0.75, font: "inherit", cursor: "pointer",
                  border: "1px solid", borderColor: on ? "text.disabled" : "divider",
                  bgcolor: on ? "action.selected" : "transparent",
                  typography: "s3", fontWeight: on ? 700 : 500, fontVariantNumeric: "tabular-nums",
                  color: on ? "text.primary" : "text.secondary",
                  "&:hover": { color: "text.primary" },
                }}
              >
                {n}
              </Box>
            );
          })}
          <Typography sx={{ typography: "s3", color: countOk ? "text.subtitle" : "error.main", ml: 0.5 }}>
            {countOk ? `Any number from ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}` : `Pick ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`}
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          The environment tests this version first. You can add scenarios and switch versions afterwards.
        </Typography>
        {/* When another environment is the right call (RSI PRD). */}
        <Stack direction="row" spacing={0.75} alignItems="flex-start" sx={{ px: 1.25, py: 1, borderRadius: 1, bgcolor: "background.neutral" }}>
          <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Create one environment per materially different setup — its data or systems, channel, market or language, or
            product line. Not one per version or batch.
          </Typography>
        </Stack>
      </Stack>
      <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
        <Button size="small" onClick={onClose} sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}>Cancel</Button>
        <Button
          size="small" variant="contained" color="primary" disabled={!name.trim() || !countOk}
          onClick={() => onBuild({ name: name.trim(), version, count })}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Build environment
        </Button>
      </Stack>
    </Dialog>
  );
}

BuildEnvironmentDialog.propTypes = {
  open: PropTypes.bool, onClose: PropTypes.func, agent: PropTypes.object.isRequired, defaultVersion: PropTypes.string, onBuild: PropTypes.func.isRequired,
  /* Built for one channel of the agent ("voice", "chat"), from its Calls settings. */
  channel: PropTypes.string,
};

function Label({ children }) {
  return <Typography sx={{ typography: "s3", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, color: "text.subtitle", mb: 0.75 }}>{children}</Typography>;
}
Label.propTypes = { children: PropTypes.node };
