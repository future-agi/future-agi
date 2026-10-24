import { Box, Button, Grid, Skeleton, Typography } from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import { emptyAgentSteps } from "./helper";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import {
  Events,
  handleOnDocsClicked,
  PropertyName,
  trackEvent,
} from "src/utils/Mixpanel";
import { useNavigate } from "react-router";

const SimulationAgentEmptyScreen = () => {
  const [isLoading, setIsLoading] = useState(true);
  const { role } = useAuthContext();
  const navigate = useNavigate();
  const handleAddAgent = () => {
    trackEvent(Events.addAgentDefClicked, { [PropertyName.click]: true });
    navigate("create-new-agent-definition");
  };
  return (
    <Box
      sx={{
        pt: "40px",
      }}
    >
      {/* Header Section */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
        }}
      >
        {/* Heading + Subheading */}
        <Box
          display="flex"
          flexDirection="column"
          gap="4px"
          alignItems="center"
        >
          <Typography
            typography="m1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            Welcome to Agent Simulation
          </Typography>
          <Typography
            typography="s1"
            fontWeight="fontWeightRegular"
            color="text.primary"
          >
            Create, configure, and test intelligent agents in minutes.
            Let&apos;s get you started with a quick tour.
          </Typography>
        </Box>

        {/* Gap between text and button */}
        <Box mt="16px">
          <Button
            variant="outlined"
            size="small"
            sx={{ width: "119px" }}
            onClick={() => {
              handleOnDocsClicked("agent-definition");
              window.open(
                "https://docs.futureagi.com/docs/simulation/concepts/agent-definition",
                "_blank",
              );
            }}
          >
            <SvgColor
              src="/assets/icons/agent/docs.svg"
              sx={{ height: 20, width: 20, mr: 1 }}
            />
            <Typography typography="s1" fontWeight="fontWeightMedium">
              View Docs
            </Typography>
          </Button>
        </Box>
      </Box>

      {/* Two-section Grid layout */}
      <Grid container spacing={2}>
        {/* Left Section */}
        <Grid
          item
          xs={12}
          md={8}
          sx={{
            display: "flex",
            justifyContent: "center",
          }}
        >
          <Box
            sx={{
              width: "100%",
              height: "100%",
              minHeight: "35vh",
            }}
          >
            <div
              style={{
                position: "relative",
                paddingBottom: "calc(53.0625% + 41px)",
                height: 0,
                width: "100%",
              }}
            >
              {isLoading && (
                <Skeleton
                  variant="rectangular"
                  sx={{
                    position: "absolute",
                    top: 22,
                    left: 0,
                    width: "100%",
                    height: "93%",
                    borderRadius: "8px",
                  }}
                />
              )}
              <iframe
                src="https://www.loom.com/embed/f13e7911fb5c4ec583c72dc20acbc83a"
                title="Knowledge Base"
                frameBorder="0"
                loading="lazy"
                allow="clipboard-write"
                allowFullScreen
                onLoad={() => setIsLoading(false)}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  colorScheme: "light",
                  borderRadius: "8px",
                  opacity: isLoading ? 0 : 1,
                  transition: "opacity 0.3s ease-in-out",
                }}
              />
            </div>
          </Box>
        </Grid>

        {/* Right Section */}
        <Grid item xs={12} md={4}>
          <Box
            display="flex"
            gap={2}
            flexDirection={"column"}
            sx={{
              mt: 2.5,
            }}
          >
            {emptyAgentSteps.map((step) => (
              <Box
                key={step.title}
                display="flex"
                alignItems="flex-start"
                gap={2}
                border={"1px solid"}
                borderColor="divider"
                borderRadius="4px"
                p={2}
              >
                {/* Icon column */}
                <SvgColor src={step.icon} sx={{ width: 28, height: 28 }} />
                {/* Text column */}
                <Box display="flex" flexDirection="column">
                  <Typography
                    typography="s1_2"
                    fontWeight="fontWeightMedium"
                    color="text.primary"
                  >
                    {step.title}
                  </Typography>
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    {step.subtitle}
                  </Typography>
                </Box>
              </Box>
            ))}
            <Button
              variant="contained"
              color="primary"
              sx={{ alignSelf: "flex-start" }}
              startIcon={
                <SvgColor
                  src="/assets/icons/ic_add.svg"
                  bgcolor="background.paper"
                  width="20px"
                />
              }
              disabled={
                !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
              }
              onClick={handleAddAgent}
            >
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                Start Agent Testing
              </Typography>
            </Button>
          </Box>
        </Grid>
      </Grid>
    </Box>
  );
};

export default SimulationAgentEmptyScreen;
