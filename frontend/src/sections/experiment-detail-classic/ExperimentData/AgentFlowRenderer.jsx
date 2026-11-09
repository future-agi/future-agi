import React, { useState, useMemo } from "react";
import { Box, Typography, Button, Switch, Stack } from "@mui/material";
import PropTypes from "prop-types";
import DatapointCard from "src/sections/common/DatapointCard";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";

const DashedConnector = () => (
  <Stack alignItems="center" sx={{ gap: 0 }}>
    <Box
      sx={{
        width: 0,
        height: 32,
        borderLeft: "2px dashed",
        borderColor: "black.300",
        marginY: "-1px",
      }}
    />

    <Box
      sx={{
        width: 16,
        height: 10,
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        marginBottom: "-4px",

        "&::after": {
          content: '""',
          position: "absolute",
          width: 8,
          height: 8,
          borderLeft: "2px solid",
          borderBottom: "2px solid",
          borderColor: "black.300",
          transform: "rotate(-45deg) translateY(-0px)",
        },
      }}
    />
  </Stack>
);

const AgentFlowRenderer = ({
  outputs,
  showDiff,
  onDiffClick,
  activeTab,
  indColsDifTracker,
  colId: _colId,
}) => {
  const [viewAllPrompts, setViewAllPrompts] = useState(false);

  const displayedOutputs = useMemo(() => {
    if (!Array.isArray(outputs) || outputs.length === 0) return [];
    if (viewAllPrompts) return outputs;
    return outputs.filter((output) => output?.isFinal);
  }, [outputs, viewAllPrompts]);

  if (!Array.isArray(outputs) || outputs.length === 0) {
    return <Typography variant="body2">No outputs</Typography>;
  }

  return (
    <Box
      sx={{ display: "flex", flexDirection: "column", gap: 1, padding: "8px" }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography={"s2_1"} fontWeight={"fontWeightMedium"}>
          Experiment details
        </Typography>
        <ShowComponent condition={outputs.length > 1}>
          <Button
            onClick={() => setViewAllPrompts((prev) => !prev)}
            variant="outlined"
            size="small"
            sx={{ padding: "8px 12px" }}
          >
            <Typography typography={"s1"} fontWeight={"fontWeightMedium"}>
              View All Prompts
            </Typography>
            <Switch
              size="small"
              sx={{
                "& .Mui-checked+.MuiSwitch-track": {
                  backgroundColor: (theme) =>
                    `${theme.palette.primary[500]} !important`,
                },
              }}
              checked={viewAllPrompts}
            />
          </Button>
        </ShowComponent>
      </Box>

      <Stack alignItems="center" gap={0}>
        <Box
          sx={{
            padding: 0.7,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor src="/assets/icons/ic_agent_flow.svg" color="blue.500" />
        </Box>

        <DashedConnector />
      </Stack>

      {displayedOutputs.map((output, index) => {
        const { columnName, isFinal, ...cellData } = output;
        const isLast = index === displayedOutputs.length - 1;

        return (
          <React.Fragment key={columnName || index}>
            <DatapointCard
              value={cellData}
              isAgentsFinalNode={isFinal}
              showDiff={showDiff}
              column={{
                dataType: "text",
                headerName: columnName || `Prompt ${index + 1}`,
              }}
              onDiffClick={onDiffClick}
              activeTab={activeTab}
              indColsDifTracker={indColsDifTracker}
            />

            {!isLast && <DashedConnector />}
          </React.Fragment>
        );
      })}
    </Box>
  );
};

AgentFlowRenderer.propTypes = {
  outputs: PropTypes.array.isRequired,
  showDiff: PropTypes.bool,
  onDiffClick: PropTypes.func,
  activeTab: PropTypes.string,
  indColsDifTracker: PropTypes.object,
  colId: PropTypes.string,
};

export default AgentFlowRenderer;
