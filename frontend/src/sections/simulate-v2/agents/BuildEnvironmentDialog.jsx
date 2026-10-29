import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useOptionalSimStore } from "../store";
import { uniqueEnvName } from "../_mock/envNames";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";
import {
  PROVIDER_LABELS,
  agentNeedsRuntime,
  runtimeOf,
} from "../_mock/agentRegistry";
import { DEFAULT_RUNTIME } from "../_mock/envConfig";
import { PARALLEL_MAX, RUN_DEFAULTS } from "../_mock/rsiModel";
import { buildCreditsCheck } from "../_mock/metering";
import useCreditBalance from "../usage/useCreditBalance";
import ConnectPreflight from "./connect/ConnectPreflight";
import { credentialCheck, factCheck, usePreflight } from "./connect/preflight";
import CopyCommandButton from "../components/CopyCommandButton";

const MONO = "ui-monospace, Menlo, monospace";
/* Quick picks; any count from 1 to 1,000 can be typed — the range the build accepts. */
const SIZES = [10, 25, 50, 100];
const MIN_SCENARIOS = 1;
const MAX_SCENARIOS = 1000;

/* The checks a build needs before it starts: the agent can be reached, its
   keys resolve, a sandbox is free, and the parallel worlds are admitted. */
const buildChecks = (agent, worlds) => {
  const v = agent?.values || {};
  const provider = PROVIDER_LABELS[v.provider] || null;
  const rt = runtimeOf(agent);
  const local = v.runLocation === "local";
  /* A provider's concurrency cap, when the connection recorded one. */
  const cap = Math.round(Number(v.maxConcurrency)) || PARALLEL_MAX;
  return [
    factCheck({
      id: "reach",
      label: "Agent",
      ok: !agentNeedsRuntime(agent),
      okText:
        rt.kind === "none"
          ? "Built fresh from its code in the sandbox."
          : `${rt.label}${rt.where ? ` · ${rt.where}` : ""} — answered.`,
      failText:
        "Nowhere to reach it yet — add its endpoint on the agent's Connections.",
    }),
    ...(v.apiKeyRef || v.hasApiKey
      ? [
          v.apiKeyRef
            ? credentialCheck({
                id: "key",
                label: "API key",
                ref: v.apiKeyRef,
                provider,
              })
            : factCheck({
                id: "key",
                label: "API key",
                ok: true,
                okText: "Held by the connection.",
              }),
        ]
      : []),
    ...(v.tokenRef
      ? [credentialCheck({ id: "token", label: "Auth token", ref: v.tokenRef })]
      : []),
    factCheck({
      id: "sandbox",
      label: "Sandbox",
      ok: true,
      okText: local
        ? "Runs on your machine through the CLI — no hosted sandbox needed."
        : `A sandbox is free in ${DEFAULT_RUNTIME.region} — the world builds there.`,
    }),
    factCheck({
      id: "worlds",
      label: "Parallel worlds",
      ok: worlds <= cap,
      okText: `${worlds} at once admitted${cap < PARALLEL_MAX ? ` — ${provider || "the provider"} allows up to ${cap}` : ""}.`,
      failText: `${provider || "The provider"} admits ${cap} at once — pick ${cap} or fewer.`,
    }),
  ];
};

/**
 * "Build environment" from an agent: the choices the builder can't make for
 * you — what to call it, which version of the agent it's built from (and
 * tests first), and how many scenarios to start with.
 * Build waits for preflight to pass and for the AI credits to cover it.
 * `defaultParallel` is the setting to start from (an environment's own,
 * when built from one); else the run default.
 */
export default function BuildEnvironmentDialog({
  open,
  onClose,
  agent,
  defaultVersion,
  onBuild,
  channel = null,
  defaultParallel = null,
}) {
  const creditBalance = useCreditBalance();
  const [name, setName] = useState("");
  const envs = useOptionalSimStore()?.state?.myEnvironments || [];
  const [version, setVersion] = useState(defaultVersion);
  /* Held as typed, so a half-typed number isn't snapped mid-edit. */
  const [countText, setCountText] = useState("10");
  const typed = Math.floor(Number(countText));
  const countOk =
    Number.isFinite(typed) && typed >= MIN_SCENARIOS && typed <= MAX_SCENARIOS;
  const count = countOk
    ? typed
    : Math.min(
        MAX_SCENARIOS,
        Math.max(MIN_SCENARIOS, Number.isFinite(typed) ? typed : MIN_SCENARIOS),
      );
  const cap = Math.round(Number(agent?.values?.maxConcurrency)) || PARALLEL_MAX;
  const startWorlds = Math.min(
    cap,
    Math.max(
      1,
      Math.round(Number(defaultParallel)) || RUN_DEFAULTS.conversationsAtOnce,
    ),
  );
  /* How many run at once is a run setting (the environment's Settings), not a build choice — the build starts from the default, and preflight checks it's admitted. */
  const worlds = startWorlds;
  /* The build writes scenarios with AI credits — over what's left, it would stop part-way. */
  const credits = countOk ? buildCreditsCheck(count, creditBalance) : null;
  const preflight = usePreflight(buildChecks(agent, worlds), [
    open,
    version,
    worlds,
  ]);

  useEffect(() => {
    if (!open) return;
    /* Numbered if taken — the name it will have, not one the store changes later. */
    setName(
      uniqueEnvName(
        `${agent.name.replace(/\s+agent$/i, "")} environment`,
        envs,
      ),
    );
    /* A draft is never what an environment tests — start from production. */
    const pick = agent.versions.find(
      (v) => v.label === (defaultVersion || agent.latest),
    );
    setVersion(
      pick && !pick.pendingDraft
        ? pick.label
        : agent.production || agent.latest,
    );
    setCountText("10");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const blocked = !name.trim()
    ? "Name it first"
    : !countOk
      ? `Scenarios: ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`
      : credits && !credits.ok
        ? "Over your AI credits — build fewer scenarios"
        : !preflight.ready
          ? preflight.status === "done"
            ? "Fix what preflight found, then run it again"
            : "Run preflight first"
          : null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 1 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {channel ? `Build a ${channel} environment` : "Build environment"}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {`We read ${agent.name} and build its environment — simulated tools, seeded data, rules and scenarios.`}
        </Typography>
      </Box>
      <Stack spacing={2} sx={{ px: 3, py: 2 }}>
        <Box>
          <Label>Name</Label>
          <TextField
            size="small"
            fullWidth
            value={name}
            onChange={(e) => setName(e.target.value)}
            InputProps={{ sx: { typography: "s2" } }}
          />
        </Box>
        <Stack direction="row" spacing={1.5}>
          <Box flex={1}>
            <Label>
              {isGitAgent(agent) ? "Built from commit" : "Built from version"}
            </Label>
            <Select
              size="small"
              fullWidth
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              sx={{ typography: "s2" }}
            >
              {[...agent.versions].reverse().map((v) => (
                <MenuItem
                  key={v.label}
                  value={v.label}
                  disabled={!!v.pendingDraft}
                  sx={{ typography: "s2" }}
                >
                  <Box
                    component="span"
                    sx={{ fontFamily: MONO, fontWeight: 600, mr: 1 }}
                  >
                    {versionNameOf(agent, v.label).main}
                  </Box>
                  <Box component="span" sx={{ color: "text.subtitle" }}>
                    {v.pendingDraft
                      ? isGitAgent(agent)
                        ? "fix branch — merge it first"
                        : "draft — release it first"
                      : v.label === agent.production
                        ? "in production"
                        : ""}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </Box>
          <Box flex={1}>
            <Label>Scenarios to start with</Label>
            <TextField
              size="small"
              fullWidth
              type="number"
              value={countText}
              placeholder="10"
              onChange={(e) => setCountText(e.target.value)}
              onBlur={() => setCountText(String(count))}
              error={!countOk}
              inputProps={{
                min: MIN_SCENARIOS,
                max: MAX_SCENARIOS,
                step: 1,
                "aria-label": "Scenarios to start with",
              }}
              InputProps={{
                sx: { typography: "s2", fontVariantNumeric: "tabular-nums" },
              }}
            />
          </Box>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mt: -1 }}
        >
          {SIZES.map((n) => (
            <Pick
              key={n}
              on={countOk && typed === n}
              onClick={() => setCountText(String(n))}
            >
              {n}
            </Pick>
          ))}
          <Typography
            sx={{
              typography: "s3",
              color: countOk ? "text.subtitle" : "error.main",
              ml: 0.5,
            }}
          >
            {countOk
              ? `Any number from ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()} · writing ${count} ≈ ${(credits?.need || 0).toLocaleString()} credits, once`
              : `Pick ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`}
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          The environment tests this version first. You can add scenarios and
          switch versions afterwards. Running them is priced separately, in the
          run plan.
        </Typography>
        {/* Out of credits for this many: said before the build, with the most that fits. */}
        {credits && !credits.ok && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              px: 1.5,
              py: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="solar:wallet-money-linear"
              width={16}
              sx={{ color: "warning.main", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Not enough AI credits for this build
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {credits.message}
              </Typography>
            </Box>
            {credits.affordable > 0 && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => setCountText(String(credits.affordable))}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                {`Build ${credits.affordable.toLocaleString()}`}
              </Button>
            )}
          </Stack>
        )}
        <ConnectPreflight preflight={preflight} required />
        {/* When another environment is the right call (RSI PRD). */}
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="flex-start"
          sx={{
            px: 1.25,
            py: 1,
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            One environment per materially different setup — data or systems,
            channel, market, language or product line. Not one per version.
          </Typography>
        </Stack>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        {blocked && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
            }}
          >
            {blocked}
          </Typography>
        )}
        {/* The same build from code — what Build environment would start. */}
        <CopyCommandButton
          command={`agent-learn env build ${(name.trim() || `${agent?.name || "my"} environment`).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")} \\\n  --agent "${agent?.name || "my-agent"}@${version}" \\\n  --request "Test it with ${count} scenarios" --budget 100`}
        />
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!!blocked}
          onClick={() =>
            onBuild({ name: name.trim(), version, count, parallel: worlds })
          }
        >
          Build environment
        </Button>
      </Stack>
    </Dialog>
  );
}

BuildEnvironmentDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object.isRequired,
  defaultVersion: PropTypes.string,
  onBuild: PropTypes.func.isRequired,
  /* Built for one channel of the agent ("voice", "chat"), from its Calls settings. */
  channel: PropTypes.string,
  /* The parallel worlds to start from — an environment's own setting, when there is one. */
  defaultParallel: PropTypes.number,
};

/* A quick-pick chip — scenario sizes, parallel worlds. */
function Pick({ on, onClick, label, children }) {
  return (
    <Box
      component="button"
      type="button"
      aria-pressed={on}
      aria-label={label}
      onClick={onClick}
      sx={{
        px: 1,
        minWidth: 28,
        height: 22,
        borderRadius: "4px",
        font: "inherit",
        cursor: "pointer",
        border: "1px solid",
        borderColor: on ? "text.disabled" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
        typography: "s3",
        fontWeight: on ? 600 : 500,
        fontVariantNumeric: "tabular-nums",
        color: on ? "text.primary" : "text.secondary",
        "&:hover": { color: "text.primary" },
      }}
    >
      {children}
    </Box>
  );
}
Pick.propTypes = {
  on: PropTypes.bool,
  onClick: PropTypes.func,
  label: PropTypes.string,
  children: PropTypes.node,
};

function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: 0.5,
        color: "text.subtitle",
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };
