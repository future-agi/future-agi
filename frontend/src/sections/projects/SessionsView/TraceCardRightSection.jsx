import PropTypes from "prop-types";
import React, { useState } from "react";
import { useParams } from "react-router";
import { Typography, Box, Button, Stack, Tabs, Tab } from "@mui/material";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import EvaluationsContent from "./EvaluationsContent";
import MetadataContent from "./MetadataContent";
import { formatMs, formatStartTimeByRequiredFormat } from "src/utils/utils";

const tabsConfig = [
  {
    label: "Evaluations",
    icon: "/assets/icons/ic_completed.svg",
  },
  {
    label: "Metadata",
    icon: "/assets/icons/navbar/ic_new_text.svg",
  },
];

const TraceCardRightSection = ({
  traceId,
  systemMetrics = {},
  evaluationMetrics = {},
}) => {
  const { observeId } = useParams();
  const [value, setValue] = React.useState(0);
  const handleChange = (event, newValue) => {
    setValue(newValue);
  };

  // Ensure systemMetrics is valid
  const safeSystemMetrics = {
    total_latency_ms: formatMs(systemMetrics?.total_latency_ms) || "N/A",
    // user_id: user?.id,
    total_cost: systemMetrics?.total_cost || "N/A",
    total_token_count: systemMetrics?.total_tokens || "N/A",
    input_tokens: systemMetrics?.input_tokens || "N/A", //DUMMY
    output_tokens: systemMetrics?.output_tokens || "N/A", // DUMMY
    start_time:
      formatStartTimeByRequiredFormat(
        systemMetrics?.start_time,
        "dd/MM/yyyy - HH:mm:ss",
      ) || "N/A",
  };

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  return (
    <Box
      sx={{
        padding: 2,
        position: "relative",
        height: "440px",
        minWidth: 0,
      }}
    >
      {/* <Grid
        container
        direction="column"
        sx={{
          position: "relative",
          minHeight: "440px",
          maxWidth: "35vw",
          flexWrap: "nowrap",
          overflow: "hidden",
        }}
      > */}
      <Stack gap={1}>
        <Typography
          typography={"m3"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          Trace Details
        </Typography>
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
          gap={1}
        >
          <Typography
            fontWeight={"fontWeightMedium"}
            typography={"s1"}
            color={"text.primary"}
          >
            Trace ID:
          </Typography>
          <Typography
            typography={"s1"}
            color={"text.primary"}
            sx={{ minWidth: 0, overflowWrap: "anywhere", textAlign: "right" }}
          >
            {traceId}
          </Typography>
        </Stack>
      </Stack>
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Tabs
          value={value}
          onChange={handleChange}
          aria-label="trace detail tabs"
          textColor="primary"
          indicatorColor="primary"
          sx={{
            typography: "s2",
            "& .Mui-selected": {
              color: "primary.main",
              fontWeight: "fontWeightSemiBold",
            },
            "& .MuiTabs-indicator": {
              backgroundColor: "primary.main",
            },
            "& .MuiTab-root": {},
          }}
        >
          {tabsConfig.map((tab, index) => (
            <Tab
              key={tab.label}
              icon={
                <SvgColor
                  sx={{
                    bgcolor: index === value ? "primary.main" : "text.disabled",
                    height: "16px",
                    width: "16px",
                  }}
                  src={tab.icon}
                />
              }
              iconPosition="start"
              label={`${tab.label} ${
                tab.label === "Evaluations"
                  ? `(${Object.keys(evaluationMetrics).length})`
                  : ""
              }`}
            />
          ))}
        </Tabs>
      </Box>
      <ShowComponent condition={value === 0}>
        <Box
          sx={{
            height: "65%",
            overflow: "auto",
          }}
        >
          <EvaluationsContent evaluationMetrics={evaluationMetrics} />
        </Box>
      </ShowComponent>
      <ShowComponent condition={value === 1}>
        <MetadataContent metadata={safeSystemMetrics} />
      </ShowComponent>

      {/* View Trace Button */}
      <Box
        p={2}
        sx={{
          position: "absolute",
          bottom: 2,
          left: 0,
          right: 0,
          bgcolor: "background.paper",
          pt: 0,
        }}
      >
        <Button
          variant="outlined"
          size="small"
          fullWidth
          sx={{ borderRadius: "8px" }}
          onClick={() => setIsDrawerOpen(true)}
          startIcon={
            <SvgColor
              src="/assets/icons/custom/eye.svg"
              sx={{
                height: "16px",
                width: "16px",
                color: "text.primary",
              }}
            />
          }
        >
          <Typography
            variant="s3"
            color="text.primary"
            fontWeight={"fontWeightMedium"}
          >
            View Trace
          </Typography>
        </Button>
      </Box>
      {/* </Grid> */}
      <TraceDetailDrawerV2
        traceId={traceId}
        open={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        projectId={observeId}
        hasPrev={false}
        hasNext={false}
      />
    </Box>
  );
};

TraceCardRightSection.propTypes = {
  traceId: PropTypes.string.isRequired,
  rowData: PropTypes.object,
  systemMetrics: PropTypes.object,
  evaluationMetrics: PropTypes.object,
};

export default TraceCardRightSection;
