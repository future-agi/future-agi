import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import SvgColor from "src/components/svg-color";
import { useOrganization } from "src/contexts/OrganizationContext";
import { RouterLink } from "src/routes/components";

import { isGatewayAdminLevel } from "../hooks/useGatewayAdmin";
import { SettingsPanel } from "../settings/ui";
import { onboardingSteps } from "./steps";

const STORAGE_PREFIX = "agentcc_getting_started_dismissed:";
// Only a per-viewer convenience. If storage is blocked, remember the choice
// for this visit, including a route unmount. Never read/migrate the old global
// key: it cannot tell us which organization's setup the user dismissed.
const visitPreferences = new Map();

function readDismissed(key) {
  if (!key) return undefined;
  try {
    const value = window.localStorage.getItem(key);
    return value === "1" ? true : value === "0" ? false : undefined;
  } catch {
    return visitPreferences.get(key);
  }
}

function writeDismissed(key, dismissed) {
  if (!key) return;
  visitPreferences.set(key, dismissed);
  try {
    window.localStorage.setItem(key, dismissed ? "1" : "0");
  } catch {
    // The in-memory preference still works for this visit.
  }
}

function StepMark({ done, index }) {
  return (
    <Box
      aria-hidden
      sx={{
        width: 24,
        height: 24,
        flexShrink: 0,
        borderRadius: "50%",
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: 600,
        border: 1,
        borderColor: done ? "success.main" : "divider",
        bgcolor: done ? "success.main" : "transparent",
        color: done ? "common.white" : "text.secondary",
      }}
    >
      {done ? (
        <SvgColor
          src="/assets/icons/ic_check.svg"
          sx={{ width: 14, height: 14, color: "common.white" }}
        />
      ) : (
        index + 1
      )}
    </Box>
  );
}
StepMark.propTypes = { done: PropTypes.bool, index: PropTypes.number };

function StepLink({ href, children }) {
  return (
    <Link component={RouterLink} href={href} typography="s2" fontWeight={500} underline="hover">
      {children}
    </Link>
  );
}
StepLink.propTypes = { href: PropTypes.string, children: PropTypes.node };

// "Try a model" for a role that cannot run it, or where the gateway does not
// serve Try it: shown, disabled, and the reason in words beside it (also the
// button's accessible description), so every role sees the same step.
function DisabledStepAction({ cta, reason, reasonId }) {
  return (
    <Stack alignItems="flex-end" spacing={0.25}>
      <Button
        size="small"
        variant="text"
        disabled
        aria-describedby={reasonId}
        sx={{ p: 0, minWidth: 0, typography: "s2", fontWeight: 500 }}
      >
        {cta}
      </Button>
      <Typography
        id={reasonId}
        typography="s3"
        color="text.secondary"
        sx={{ maxWidth: 240, textAlign: "right" }}
      >
        {reason}
      </Typography>
    </Stack>
  );
}
DisabledStepAction.propTypes = {
  cta: PropTypes.string.isRequired,
  reason: PropTypes.string.isRequired,
  reasonId: PropTypes.string.isRequired,
};

// BP-01 passes setupReadiness' result, the useRequestCount query object and a
// read-only onCheckAgain callback. This card never issues an independent query
// or a billed model request. Count-only callers remain supported.
export default function GettingStarted({
  loading = false,
  readiness,
  requestQuery,
  onCheckAgain,
  canRunConsole,
  ...counts
}) {
  const { currentOrganizationId, orgLevel } = useOrganization();
  const [preferences, setPreferences] = useState({});
  const idPrefix = useId();
  const storageKey = currentOrganizationId == null
    ? null
    : `${STORAGE_PREFIX}${encodeURIComponent(currentOrganizationId)}`;
  const preferenceKey = storageKey ?? "unscoped";
  const dismissed = Object.prototype.hasOwnProperty.call(preferences, preferenceKey)
    ? preferences[preferenceKey]
    : readDismissed(storageKey);
  const setDismissed = (value) => {
    writeDismissed(storageKey, value);
    setPreferences((previous) => ({ ...previous, [preferenceKey]: value }));
  };
  if (loading) return null;

  const steps = onboardingSteps({
    ...counts,
    readiness,
    requestQuery,
    canRunConsole: canRunConsole ?? isGatewayAdminLevel(orgLevel),
  });
  const doneCount = steps.filter((step) => step.done).length;
  if (dismissed || (doneCount === steps.length && dismissed !== false)) {
    return (
      <Box>
        <Button size="small" color="inherit" onClick={() => setDismissed(false)}>
          Show setup
        </Button>
      </Box>
    );
  }
  const next = steps.find((step) => !step.done);
  const checking = Boolean(requestQuery?.isFetching);

  return (
    <SettingsPanel
      title="Get started"
      description={`${doneCount} of ${steps.length} done`}
      testId="overview-getting-started"
      actions={
        <Button size="small" color="inherit" onClick={() => setDismissed(true)}>
          Dismiss
        </Button>
      }
    >
      <Stack component="ol" spacing={1.5} sx={{ m: 0, p: 0, listStyle: "none" }}>
        {steps.map((step, index) => (
          <Box
            component="li"
            key={step.id}
            aria-current={step === next ? "step" : undefined}
            aria-label={`${step.title}: ${step.done ? "Complete" : step.status === "unknown" ? "Not checked" : "Not complete"}`}
            sx={{ display: "flex", gap: 1.5, alignItems: "flex-start", flexWrap: "wrap" }}
          >
            <StepMark done={step.done} index={index} />
            <Box sx={{ minWidth: 0, flex: "1 1 180px" }}>
              <Typography
                typography="s1"
                fontWeight={step === next ? 600 : 400}
                color={step.done ? "text.secondary" : "text.primary"}
              >
                {step.title}
              </Typography>
              {!step.done && (
                <Typography typography="s2" color="text.secondary">
                  {step.description}
                </Typography>
              )}
            </Box>
            {(step === next || (!step.done && step.tryModel)) && (
              <Stack alignItems="flex-end" spacing={0.5} sx={{ flexShrink: 0, pt: 0.25 }}>
                {!step.done && step.tryModel && (
                  step.tryModel.disabled ? (
                    <DisabledStepAction
                      cta={step.tryModel.cta}
                      reason={step.tryModel.reason}
                      reasonId={`${idPrefix}-${step.id}-try-reason`}
                    />
                  ) : (
                    <StepLink href={step.tryModel.href}>{step.tryModel.cta}</StepLink>
                  )
                )}
                {step === next && <StepLink href={step.href}>{step.cta}</StepLink>}
              </Stack>
            )}
          </Box>
        ))}
      </Stack>
      {onCheckAgain && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2 }}>
          <Button size="small" onClick={onCheckAgain} disabled={checking}>
            {checking ? "Checking…" : "Check again"}
          </Button>
          <Typography typography="s2" color="text.secondary" role="status">
            {checking ? "Reading setup progress…" : "Checks setup progress without sending a model request."}
          </Typography>
        </Stack>
      )}
    </SettingsPanel>
  );
}

const readinessState = PropTypes.oneOf(["ready", "not-ready", "unknown"]);
GettingStarted.propTypes = {
  loading: PropTypes.bool,
  providerCount: PropTypes.number,
  managedOn: PropTypes.bool,
  managedOffered: PropTypes.bool,
  keyCount: PropTypes.number,
  requestCount: PropTypes.number,
  tryModelHref: PropTypes.string,
  // Whether the gateway serves Try it; Try a model is a link only then.
  tryItAvailable: PropTypes.bool,
  // What the disabled Try a model says where Try it does not run.
  tryItUnavailableReason: PropTypes.string,
  readiness: PropTypes.shape({ source: readinessState, key: readinessState, managed: readinessState }),
  requestQuery: PropTypes.shape({
    data: PropTypes.number,
    isError: PropTypes.bool,
    isPending: PropTypes.bool,
    isLoading: PropTypes.bool,
    isFetching: PropTypes.bool,
    error: PropTypes.any,
  }),
  onCheckAgain: PropTypes.func,
  canRunConsole: PropTypes.bool,
};
