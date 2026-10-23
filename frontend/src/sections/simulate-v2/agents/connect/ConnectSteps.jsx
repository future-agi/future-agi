import PropTypes from "prop-types";
import { Fragment, useContext } from "react";
import { Box, Button, CircularProgress, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { InDrawerContext } from "../../environments/connectMode";
import ConnectPreflight from "./ConnectPreflight";
import { MAX_BUILD_SCENARIOS, useConnectBuild } from "./connectSubmit";
import { buildCreditsCheck } from "../../_mock/metering";

/**
 * The frame every New agent flow walks through: where you are, the step
 * itself, and Back / Continue. Same card, same footer as the environment
 * connect panels — the steps are what's new, so they get a quiet strip
 * rather than a stepper that shouts.
 */
export default function ConnectSteps({ title, subtitle, steps, active, onBack, next: asked, preflight = null, children }) {
  /* In a drawer the header already names the flow — just the steps here. */
  const inDrawer = useContext(InDrawerContext);
  /* Connecting for a new environment: the form also asks how many scenarios
     to generate, and the build waits for preflight to pass. */
  const build = useConnectBuild();
  const count = build ? Number(build.scenarioCount) : null;
  const countOk = !build || (Number.isInteger(count) && count >= 1 && count <= MAX_BUILD_SCENARIOS);
  /* The build writes scenarios with AI credits — over what's left, it would stop part-way. */
  const credits = build && countOk ? buildCreditsCheck(count) : null;
  const waitsForPreflight = !!build && !!preflight && !preflight.state.ready;
  const next = asked.disabled
    ? asked
    : !countOk
      ? { ...asked, disabled: true, hint: `Scenarios to generate: 1 to ${MAX_BUILD_SCENARIOS.toLocaleString()}` }
      : credits && !credits.ok
        ? { ...asked, disabled: true, hint: "Over your AI credits — build fewer scenarios" }
      : waitsForPreflight
        ? { ...asked, disabled: true, hint: preflight.state.status === "done" ? "Fix what preflight found, then run it again" : "Run preflight first" }
        : asked;
  /* One step is no journey — no strip, and in a drawer no header at all. */
  const single = steps.length <= 1;
  const showHead = !(inDrawer && single);
  return (
    /* `clip`, not `hidden`: hidden would make this the scroll box and stop
       the footer sticking to the drawer's edge. */
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, bgcolor: "background.paper", overflow: "clip" }}>
      {showHead && (
      <Box sx={{ px: 2.5, pt: inDrawer ? 1.25 : 1.75, pb: inDrawer ? 1.25 : 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
        {!inDrawer && <Typography sx={{ typography: "s1", fontWeight: 600 }}>{title}</Typography>}
        {!inDrawer && subtitle && <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{subtitle}</Typography>}
        {!single && (
        <Stack direction="row" alignItems="center" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: inDrawer ? 0 : 1.5 }}>
          {steps.map((label, i) => (
            <Fragment key={label}>
              {i > 0 && <Box sx={{ width: 20, height: "1px", bgcolor: "divider", flexShrink: 0 }} />}
              <StepDot n={i + 1} label={label} state={i < active ? "done" : i === active ? "on" : "next"} />
            </Fragment>
          ))}
        </Stack>
        )}
      </Box>
      )}

      <Box sx={{ p: 2.5 }}>
        {/* In a drawer with one step, the subtitle is the only framing — said here. */}
        {!showHead && subtitle && <Typography sx={{ typography: "s2", color: "text.subtitle", mb: 1.75 }}>{subtitle}</Typography>}
        {children}
        {(build || preflight) && (
          <Stack spacing={1.25} sx={{ mt: 1.75 }}>
            {build && <ScenarioCount value={build.scenarioCount} onChange={build.setScenarioCount} ok={countOk} credits={credits} />}
            {preflight && <ConnectPreflight preflight={preflight.state} required={!!build} canRun={preflight.canRun !== false} blockedReason={preflight.blockedReason} summary={preflight.summary} />}
          </Stack>
        )}
      </Box>

      {/* In a drawer the submit row stays pinned to its bottom edge while a
          long form (a monorepo's agents) scrolls behind it. */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{
          px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider",
          ...(inDrawer && { position: "sticky", bottom: 0, zIndex: 2, bgcolor: "background.paper" }),
        }}
      >
        {onBack ? (
          <Button
            onClick={onBack}
            startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={14} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
          >
            Back
          </Button>
        ) : <Box />}
        <Box flex={1} />
        {next.busy ? (
          <Stack direction="row" alignItems="center" spacing={1}>
            <CircularProgress size={13} thickness={5} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{next.busy}</Typography>
          </Stack>
        ) : next.disabled && next.hint ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{next.hint}</Typography>
        ) : null}
        <Button
          variant="contained"
          color="primary"
          disabled={!!next.disabled || !!next.busy}
          onClick={next.onClick}
          startIcon={next.icon ? <Iconify icon={next.icon} width={14} /> : null}
          endIcon={next.icon ? null : <Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{ typography: "s1", fontWeight: 700, px: 2, flexShrink: 0 }}
        >
          {next.label || "Continue"}
        </Button>
      </Stack>
    </Box>
  );
}

ConnectSteps.propTypes = {
  title: PropTypes.node,
  subtitle: PropTypes.node,
  steps: PropTypes.arrayOf(PropTypes.string).isRequired,
  active: PropTypes.number.isRequired,
  onBack: PropTypes.func,
  /* { label, onClick, disabled, hint, busy, icon } — `busy` is the line shown while it works. */
  next: PropTypes.object.isRequired,
  /* { state: usePreflight(), canRun, blockedReason, summary } — the flow's checks. */
  preflight: PropTypes.object,
  children: PropTypes.node,
};

/* How many scenarios the build generates — the same range the product's build accepts. */
function ScenarioCount({ value, onChange, ok, credits = null }) {
  const n = Number(value);
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.5, py: 1 }}>
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>Scenarios to generate</Typography>
        <Typography sx={{ typography: "s3", color: ok ? "text.subtitle" : "error.main" }}>
          {ok ? `We'll generate ${n.toLocaleString()} scenario${n === 1 ? "" : "s"} when it builds. Set any number up to ${MAX_BUILD_SCENARIOS.toLocaleString()}.` : `Between 1 and ${MAX_BUILD_SCENARIOS.toLocaleString()}.`}
        </Typography>
      </Box>
      <TextField
        size="small" type="number" value={value} placeholder="10"
        onChange={(e) => onChange(e.target.value)}
        inputProps={{ min: 1, max: MAX_BUILD_SCENARIOS, "aria-label": "Scenarios to generate" }}
        sx={{ width: 96, flexShrink: 0, "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", py: 0.75 } }}
      />
    </Stack>
    {/* Out of credits for this many: said before the build, with the most that fits. */}
    {credits && !credits.ok && (
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 1.5, py: 1, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
        <Iconify icon="solar:wallet-money-linear" width={16} sx={{ color: "warning.main", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>Not enough AI credits for this build</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{credits.message}</Typography>
        </Box>
        {credits.affordable > 0 && (
          <Button size="small" variant="outlined" onClick={() => onChange(String(credits.affordable))} sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
            {`Build ${credits.affordable.toLocaleString()}`}
          </Button>
        )}
      </Stack>
    )}
    </Box>
  );
}
ScenarioCount.propTypes = { value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]), onChange: PropTypes.func.isRequired, ok: PropTypes.bool, credits: PropTypes.object };

function StepDot({ n, label, state }) {
  const on = state === "on";
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Box
        sx={{
          width: 18, height: 18, borderRadius: "50%", display: "grid", placeItems: "center", flexShrink: 0,
          border: "1px solid",
          borderColor: on ? "text.primary" : "divider",
          bgcolor: on ? "text.primary" : "transparent",
          color: on ? "background.paper" : "text.secondary",
          typography: "s3", fontWeight: 700, lineHeight: 1,
        }}
      >
        {state === "done" ? <Iconify icon="solar:check-read-linear" width={12} /> : n}
      </Box>
      <Typography sx={{ typography: "s2", fontWeight: on ? 700 : 500, color: state === "next" ? "text.subtitle" : "text.primary", whiteSpace: "nowrap" }}>
        {label}
      </Typography>
    </Stack>
  );
}
StepDot.propTypes = { n: PropTypes.number, label: PropTypes.string, state: PropTypes.oneOf(["done", "on", "next"]) };
