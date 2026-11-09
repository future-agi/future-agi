import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";

import { SettingsPanel } from "../settings/ui";
import { onboardingSteps } from "./steps";

// Kept from the old card, so a user who dismissed it does not see it again.
const STORAGE_KEY = "agentcc_getting_started_dismissed";

function readDismissed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeDismissed() {
  try {
    window.localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // Storage can be blocked; the card then only hides for this visit.
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
    <Link
      component={RouterLink}
      href={href}
      typography="s2"
      fontWeight={500}
      underline="hover"
    >
      {children}
    </Link>
  );
}

StepLink.propTypes = { href: PropTypes.string, children: PropTypes.node };

// "Get started" on the Overview: the steps, which are done, and a link to do
// the next one. Hidden once all are done, or after Dismiss.
export default function GettingStarted({ loading = false, ...counts }) {
  const [dismissed, setDismissed] = useState(readDismissed);
  if (loading || dismissed) return null;

  const steps = onboardingSteps(counts);
  const doneCount = steps.filter((step) => step.done).length;
  if (doneCount === steps.length) return null;
  const next = steps.find((step) => !step.done);

  return (
    <SettingsPanel
      title="Get started"
      description={`${doneCount} of ${steps.length} done`}
      testId="overview-getting-started"
      actions={
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            writeDismissed();
            setDismissed(true);
          }}
        >
          Dismiss
        </Button>
      }
    >
      <Stack
        component="ol"
        spacing={1.5}
        sx={{ m: 0, p: 0, listStyle: "none" }}
      >
        {steps.map((step, index) => (
          <Box
            component="li"
            key={step.id}
            aria-current={step === next ? "step" : undefined}
            sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}
          >
            <StepMark done={step.done} index={index} />
            <Box sx={{ minWidth: 0, flex: 1 }}>
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
              <Stack
                alignItems="flex-end"
                spacing={0.5}
                sx={{ flexShrink: 0, pt: 0.25 }}
              >
                {/* Try a model needs no API key, so it is offered until the
                    first request, whichever step is next. */}
                {!step.done && step.tryModel && (
                  <StepLink href={step.tryModel.href}>
                    {step.tryModel.cta}
                  </StepLink>
                )}
                {step === next && (
                  <StepLink href={step.href}>{step.cta}</StepLink>
                )}
              </Stack>
            )}
          </Box>
        ))}
      </Stack>
    </SettingsPanel>
  );
}

GettingStarted.propTypes = {
  loading: PropTypes.bool,
  providerCount: PropTypes.number,
  managedOn: PropTypes.bool,
  managedOffered: PropTypes.bool,
  keyCount: PropTypes.number,
  requestCount: PropTypes.number,
  // The Try it tab of the first model a provider of the org serves.
  tryModelHref: PropTypes.string,
};
