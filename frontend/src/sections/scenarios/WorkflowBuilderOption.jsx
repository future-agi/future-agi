import { Box, Button, Typography } from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import SwitchField from "src/components/Switch/SwitchField";
import PropTypes from "prop-types";
import { useController, useWatch } from "react-hook-form";
import { ShowComponent } from "src/components/show";
import GraphBuilderDrawer from "src/components/GraphBuilder/GraphBuilderDrawer";
import CustomTooltip from "src/components/tooltip";

const WorkflowBuilderOption = ({ control }) => {
  const generateGraph = useWatch({ control, name: "config.generateGraph" });
  const graphControl = useController({ control, name: "config.graph" });
  const agentType = useWatch({ control, name: "agentType" });

  const [open, setOpen] = useState(false);
  return (
    <Box
      sx={{
        height: "210px",
        border: "1px solid",
        borderColor: "primary.light",
        borderRadius: "4px",
        backgroundColor: "action.hover",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        gap: "12px",
      }}
    >
      <SvgColor
        src={`/assets/icons/navbar/ic_sessions.svg`}
        sx={{ width: "32px", height: "32px", color: "primary.main" }}
      />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 0.5,
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Workflow builder
        </Typography>
        <Typography typography="s2" color="text.primary">
          Use our visual builder to create conversation flows
        </Typography>
      </Box>
      <GraphBuilderDrawer
        open={open}
        agentType={agentType}
        onClose={() => setOpen(false)}
        value={graphControl.field.value}
        onChange={(value) => {
          graphControl.field.onChange(value);
          setOpen(false);
        }}
      />
      <Box sx={{ gap: 1, display: "flex", flexDirection: "column" }}>
        <CustomTooltip
          show={true}
          title={
            generateGraph
              ? "Automatically generates scenarios based on your agent definition and description"
              : "Manually build conversation flows using the visual graph builder"
          }
          placement="bottom"
          arrow
          size="small"
          type="black"
          slotProps={{
            tooltip: {
              sx: {
                maxWidth: "200px !important",
              },
            },
          }}
        >
          <Box>
            <SwitchField
              control={control}
              fieldName="config.generateGraph"
              label="Auto Generate Graph"
              labelPlacement="start"
              labelStyle={{ fontSize: "13px", fontWeight: "fontWeightMedium" }}
            />
          </Box>
        </CustomTooltip>

        <ShowComponent condition={!generateGraph}>
          <CustomTooltip
            type="black"
            size="small"
            show={!agentType}
            title="Select agent definition to enable"
            arrow
          >
            <span style={{ display: "inline-block" }}>
              <Button
                size="small"
                variant="outlined"
                color="primary"
                onClick={() => setOpen(true)}
                disabled={!agentType}
                sx={{ cursor: "pointer" }}
              >
                Manually Create Workflow
              </Button>
            </span>
          </CustomTooltip>
        </ShowComponent>
      </Box>
    </Box>
  );
};

WorkflowBuilderOption.propTypes = {
  control: PropTypes.object,
};

export default WorkflowBuilderOption;
