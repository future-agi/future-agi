import React from "react";
import PropTypes from "prop-types";
import { Typography, Stack, Button, Link } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import Section from "src/components/section";

const STORAGE_KEY = "agentcc_getting_started_dismissed";

const STEPS = [
  {
    label: "Gateway connected",
    key: "gatewayConnected",
    icon: "solar:shield-check-linear",
  },
  {
    label: "Add a provider",
    key: "hasProviders",
    icon: "solar:cloud-linear",
    cta: "Configure providers",
    path: "/dashboard/gateway/providers",
  },
  {
    label: "Create an API key",
    key: "hasKeys",
    icon: "solar:key-linear",
    cta: "Create key",
    path: "/dashboard/gateway/keys",
  },
  {
    label: "Send your first request",
    key: "hasRequests",
    icon: "solar:send-square-linear",
    cta: "View docs",
    href: "https://docs.futureagi.com/docs/command-center",
  },
];

function isDismissed() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function dismiss() {
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // noop
  }
}

const GettingStartedCard = ({ completionState, onDismiss }) => {
  const navigate = useNavigate();
  const [dismissed, setDismissed] = React.useState(isDismissed);

  const allComplete = STEPS.every((s) => completionState[s.key]);

  if (dismissed || allComplete) return null;

  const completedCount = STEPS.filter((s) => completionState[s.key]).length;

  const handleDismiss = () => {
    dismiss();
    setDismissed(true);
    onDismiss?.();
  };

  return (
    <Section
      title="Get started with Agent Command Center Gateway"
      sx={{ mb: 3 }}
      actions={
        <>
          <Typography sx={{ fontSize: 12, color: "text.tertiary" }}>
            {completedCount}/{STEPS.length}
          </Typography>
          <Link
            component="button"
            variant="caption"
            underline="hover"
            color="text.secondary"
            onClick={handleDismiss}
          >
            Dismiss
          </Link>
        </>
      }
    >
      <Stack spacing={1.5}>
        {STEPS.map((step) => {
          const done = completionState[step.key];
          return (
            <Stack
              key={step.key}
              direction="row"
              alignItems="center"
              spacing={1.5}
            >
              <Iconify
                icon={done ? "solar:check-circle-linear" : step.icon}
                width={16}
                sx={{
                  color: done ? "status.success.fg" : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography
                sx={{
                  flex: 1,
                  fontSize: 13,
                  color: done ? "text.disabled" : "text.primary",
                }}
              >
                {step.label}
              </Typography>
              {!done && step.cta && (
                <Button
                  size="small"
                  variant="text"
                  onClick={() => {
                    if (step.href) {
                      window.open(step.href, "_blank", "noopener");
                    } else if (step.path) {
                      navigate(step.path);
                    }
                  }}
                  endIcon={
                    <Iconify icon="solar:arrow-right-linear" width={16} />
                  }
                  sx={{ whiteSpace: "nowrap" }}
                >
                  {step.cta}
                </Button>
              )}
            </Stack>
          );
        })}
      </Stack>
    </Section>
  );
};

GettingStartedCard.propTypes = {
  completionState: PropTypes.shape({
    gatewayConnected: PropTypes.bool,
    hasProviders: PropTypes.bool,
    hasKeys: PropTypes.bool,
    hasRequests: PropTypes.bool,
  }).isRequired,
  onDismiss: PropTypes.func,
};

export default GettingStartedCard;
