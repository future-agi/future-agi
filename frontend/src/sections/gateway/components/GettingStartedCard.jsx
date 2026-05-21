import React from "react";
import PropTypes from "prop-types";
import {
  Card,
  CardContent,
  Typography,
  Stack,
  Button,
  Link,
  Box,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";

const STORAGE_KEY = "agentcc_getting_started_dismissed";

const STEPS = [
  {
    label: "Gateway Connected",
    key: "gatewayConnected",
    icon: "mdi:check-network",
  },
  {
    label: "Add a Provider",
    key: "hasProviders",
    icon: "mdi:cloud-outline",
    cta: "Configure Providers",
    path: "/dashboard/gateway/providers",
  },
  {
    label: "Create an API Key",
    key: "hasKeys",
    icon: "mdi:key-outline",
    cta: "Create Key",
    path: "/dashboard/gateway/keys",
  },
  {
    label: "Send Your First Request",
    key: "hasRequests",
    icon: "mdi:send-outline",
    cta: "View Docs",
    href: "https://docs.futureagi.com/docs/command-center/quickstart",
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
    <Card sx={{ mb: 3, border: 1, borderColor: "primary.main" }}>
      <CardContent sx={{ py: 2.5 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Stack direction="row" spacing={1} alignItems="center">
            <Iconify
              icon="mdi:rocket-launch-outline"
              width={22}
              sx={{ color: "primary.main" }}
            />
            <Typography variant="h6">Get Started with Agent Command Center Gateway</Typography>
          </Stack>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Typography variant="caption" color="text.secondary">
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
          </Stack>
        </Stack>

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
                <Box
                  sx={{
                    width: 28,
                    height: 28,
                    borderRadius: "50%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    bgcolor: done ? "success.main" : "action.hover",
                    flexShrink: 0,
                  }}
                >
                  <Iconify
                    icon={done ? "mdi:check" : step.icon}
                    width={16}
                    sx={{ color: done ? "common.white" : "text.secondary" }}
                  />
                </Box>
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    textDecoration: done ? "line-through" : "none",
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
                    endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
                    sx={{ textTransform: "none", whiteSpace: "nowrap" }}
                  >
                    {step.cta}
                  </Button>
                )}
              </Stack>
            );
          })}
        </Stack>
      </CardContent>
    </Card>
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
