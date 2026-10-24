import {
  Box,
  Button,
  Divider,
  Grid,
  Link,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Image from "src/components/image";
import { ShowComponent } from "src/components/show";
import { handleOnDocsClicked } from "../../../../utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const getAlertInstructions = (isDark) => [
  {
    title: "Define Your Metric",
    description:
      "Select the metric (system or eval) you want to create an alert for, ex: Latency or hallucination %",
    image: isDark
      ? "/assets/images/monitors/metrics_dark.png"
      : "/assets/images/monitors/metrics.png",
  },
  {
    title: "Set Alert Conditions",
    description:
      "Configure notification thresholds by choosing greater than/less than and specifying the value that triggers alerts.",
    image: isDark
      ? "/assets/images/monitors/data_dark.png"
      : "/assets/images/monitors/data.png",
  },
  {
    title: "Configure Notifications",
    description:
      "Choose who will receive alerts by adding emails and check the alerts by visiting logs whenever required!",
    image: isDark
      ? "/assets/images/monitors/alerts_dark.png"
      : "/assets/images/monitors/alerts.png",
  },
];

export default function EmptyAlerts({ onStartCreatingAlerts, mainPage }) {
  const { role } = useAuthContext();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const alertInstruction = getAlertInstructions(isDark);
  return (
    <Box
      sx={{
        padding: theme.spacing(2, 1.5),
        display: "flex",
        flexDirection: "column",
        gap: theme.spacing(2),
        ...(mainPage && {
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }),
      }}
    >
      <Stack
        gap={0.5}
        sx={{
          ...(mainPage && {
            alignItems: "center",
          }),
        }}
      >
        {/* On the Alerts page the page header already says this; only an
            embedded empty state (inside a project) needs its own title. */}
        <ShowComponent condition={!mainPage}>
          <Typography
            color={"text.primary"}
            variant="m2"
            fontWeight={"fontWeightSemiBold"}
          >
            Alerts
          </Typography>
        </ShowComponent>
      </Stack>
      <Grid container justifyContent="center">
        {alertInstruction.map((alert, index) => (
          <Grid item xs={12} sm={4} key={index} sx={{ textAlign: "left" }}>
            <Box
              paddingX={theme.spacing(1.5)}
              display={"flex"}
              flexDirection={"column"}
            >
              <Box
                sx={{
                  backgroundColor: "background.neutral",
                  borderRadius: theme.spacing(1.5),
                  border: "1px solid",
                  borderColor: "divider",
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center", // center vertically
                  padding: theme.spacing(3.5, 4.25),
                  paddingBottom: 0,
                }}
              >
                <Image
                  src={alert.image}
                  alt={alert.title}
                  sx={{
                    maxHeight: 300, // control max height
                    width: "100%", // responsive width
                    objectFit: "contain", // prevents cutting
                    borderRadius: theme.spacing(0.5),
                  }}
                />
              </Box>

              <Typography
                typography="m2"
                fontWeight={"fontWeightSemiBold"}
                color="text.primary"
                mt={theme.spacing(2)}
              >
                {alert.title}
              </Typography>
              <Typography
                typography="m3"
                fontWeight={"fontWeightRegular"}
                color="text.disabled"
              >
                {alert.description}
              </Typography>
            </Box>
          </Grid>
        ))}
      </Grid>
      <Box
        sx={{
          my: 3,
        }}
      >
        <Divider />
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          width: "100%",
        }}
      >
        <Typography
          color="text.primary"
          typography="m3"
          fontWeight="fontWeightSemiBold"
          sx={{ marginBottom: theme.spacing(2) }}
        >
          For more instructions, check out our{" "}
          <Link
            href="https://docs.futureagi.com/docs/observe/features/alerts"
            underline="always"
            color="primary.main"
            target="_blank"
            onClick={() =>
              handleOnDocsClicked(
                mainPage ? "alerts_main_page" : "observe_tab_alerts",
              )
            }
          >
            Docs
          </Link>
        </Typography>
        <Button
          variant="contained"
          color="primary"
          onClick={() => {
            onStartCreatingAlerts();
          }}
          disabled={
            !RolePermission.OBSERVABILITY[PERMISSIONS.ADD_TASKS_ALERTS][role]
          }
        >
          Start creating alerts
        </Button>
      </Box>
    </Box>
  );
}

EmptyAlerts.displayName = "EmptyAlerts";

EmptyAlerts.propTypes = {
  onStartCreatingAlerts: PropTypes.func,
  mainPage: PropTypes.bool,
};
