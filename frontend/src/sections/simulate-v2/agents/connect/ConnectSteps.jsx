import PropTypes from "prop-types";
import { Fragment, useContext } from "react";
import { Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { InDrawerContext } from "../../environments/connectMode";

/**
 * The frame every New agent flow walks through: where you are, the step
 * itself, and Back / Continue. Same card, same footer as the environment
 * connect panels — the steps are what's new, so they get a quiet strip
 * rather than a stepper that shouts.
 */
export default function ConnectSteps({ title, subtitle, steps, active, onBack, next, children }) {
  /* In a drawer the header already names the flow — just the steps here. */
  const inDrawer = useContext(InDrawerContext);
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
  children: PropTypes.node,
};

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
