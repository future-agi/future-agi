import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import { AgentPromptOptimizerStatus } from "../FixMyAgentDrawer/common";

const OptmizationLoaderComponent = ({
  optimizationStatus = "unknown",
  reason,
}) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        height: "490px",
        minHeight: "490px",
        width: "100%",
      }}
    >
      <ShowComponent
        condition={optimizationStatus === AgentPromptOptimizerStatus.RUNNING}
      >
        <Stack
          spacing={2}
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
        >
          <Box
            sx={{
              width: "60px",
              height: "60px",
              borderRadius: "100%",
              backgroundColor: "action.hover",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={24} sx={{ color: "text.primary" }} />
          </Box>

          <Stack
            spacing={0.5}
            textAlign="center"
            display={"flex"}
            alignItems={"center"}
          >
            <Typography variant="m3" fontWeight="fontWeightMedium">
              Please wait while we complete the optimization...
            </Typography>

            <Typography
              variant="s1"
              fontWeight="fontWeightRegular"
              color="text.secondary"
              sx={{ width: "250px" }}
            >
              We are optimizing your agent’s issues, this might take some time.
            </Typography>
          </Stack>
        </Stack>
      </ShowComponent>
      <ShowComponent
        condition={optimizationStatus === AgentPromptOptimizerStatus.PENDING}
      >
        <Stack
          spacing={2}
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
        >
          <Box
            sx={{
              width: "60px",
              height: "60px",
              borderRadius: "100%",
              backgroundColor: "action.hover",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <CircularProgress size={24} sx={{ color: "text.primary" }} />
          </Box>

          <Stack
            spacing={0.5}
            textAlign="center"
            display={"flex"}
            alignItems={"center"}
          >
            <Typography variant="m3" fontWeight="fontWeightMedium">
              Optimization in queue
            </Typography>

            <Typography
              variant="s1"
              fontWeight="fontWeightRegular"
              sx={{ width: "250px" }}
            >
              Your optimization run is waiting to start.
            </Typography>
          </Stack>
        </Stack>
      </ShowComponent>
      {/* <ShowComponent condition={optimizationStatus === AgentPromptOptimizerStatus.FAILED}>
        <Stack
          spacing={2}
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
        >
          <Box
            sx={{
              width: "60px",
              height: "60px",
              borderRadius: "100%",
              backgroundColor: "action.hover",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              src="/assets/icons/ic_critical.svg"
              sx={{
                height: 22,
                width: 22,
                bgcolor: "red.500",
              }}
            />
          </Box>

          <Stack
            spacing={0.25}
            textAlign="center"
            display={"flex"}
            alignItems={"center"}
          >
            <Typography variant="m3" fontWeight="fontWeightMedium">
              Failed to optimize
            </Typography>

            <Typography
              variant="s1"
              fontWeight="fontWeightRegular"
              sx={{
                ...(optimizationStatus === "running" && { width: "250px" }),
              }}
            >
              Your optimization has failed due to insufficient balance
            </Typography>
            <Button
              variant="outlined"
              size="medium"
              startIcon={
                <SvgColor
                  sx={{
                    color: "text.primary",
                    height: 16,
                    width: 16,
                  }}
                  src={"/assets/icons/ic_add.svg"}
                />
              }
              onClick={() => {
                navigate("/dashboard/settings/billing");
              }}
            >
              Add Credits
            </Button>
          </Stack>
        </Stack>
      </ShowComponent> */}
      <ShowComponent
        condition={optimizationStatus === AgentPromptOptimizerStatus.FAILED}
      >
        <Stack
          spacing={2}
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
        >
          <Box
            sx={{
              borderRadius: "100%",

              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <img
              src="/assets/errorfallback/something_went_wrong.svg"
              alt=""
              style={{
                width: "200px",
                height: "200px",
              }}
            />
          </Box>

          <Stack
            spacing={0.25}
            textAlign="center"
            display={"flex"}
            alignItems={"center"}
          >
            <Typography variant="m3" fontWeight="fontWeightMedium">
              Failed to optimize
            </Typography>

            <Typography
              variant="s1"
              fontWeight="fontWeightRegular"
              sx={{
                ...(optimizationStatus === "running" && { width: "250px" }),
              }}
            >
              {reason ||
                "An unknown issue occurred while processing your optimization."}
            </Typography>
            {/* <Button
              variant="outlined"
              size="medium"
              startIcon={
                <Iconify icon="pajamas:retry" width="22px" height="22px" />
              }
              onClick={() => {
                navigate("/dashboard/settings/billing");
              }}
            >
              Retry
            </Button> */}
          </Stack>
        </Stack>
      </ShowComponent>
    </Box>
  );
};

OptmizationLoaderComponent.propTypes = {
  optimizationStatus: PropTypes.string,
  reason: PropTypes.string,
};

export default OptmizationLoaderComponent;
