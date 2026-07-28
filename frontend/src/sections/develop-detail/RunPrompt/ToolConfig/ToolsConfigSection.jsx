import React, { useMemo } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../../AccordianElements";
import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import SvgColor from "src/components/svg-color";
import SelectedItemWithActions from "src/components/SelectedItemWithActions";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";

const ToolsConfigSection = ({
  control,
  setValue,
  selectedTools,
  onOpenCustomToolModal,
  setEditTool,
}) => {
  const { data: runPromptOptions } = useRunPromptOptions();
  const theme = useTheme();

  const toolsOptions = useMemo(() => {
    return runPromptOptions?.available_tools?.map((t) => ({
      label: t.name,
      value: t.id,
      tool: t,
    }));
  }, [runPromptOptions?.available_tools]);

  const filteredToolOptions = useMemo(() => {
    return (toolsOptions || [])?.filter((tool) =>
      selectedTools?.includes(tool?.value),
    );
  }, [toolsOptions, selectedTools]);

  // Handle removing a tool from selection
  const handleRemoveTool = (toolValue) => {
    setValue(
      "config.tools",
      selectedTools?.filter((tool) => tool !== toolValue),
      {
        shouldDirty: true,
      },
    );
  };

  return (
    <>
      <Accordion
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          padding: "16px",
        }}
      >
        <AccordionSummary
          sx={{
            flexDirection: "row",
            minHeight: "unset",
            padding: 0,
            "& .MuiAccordionSummary-content": {
              padding: 0,
              margin: 0,
            },
            "& .MuiAccordionSummary-expandIconWrapper": {
              transform: "rotate(90deg)",
              "& > svg": {
                color: "text.primary",
              },
            },
            "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
              transform: "rotate(270deg)",
            },
          }}
        >
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Tool Configuration
          </Typography>
        </AccordionSummary>
        <AccordionDetails
          sx={{
            padding: 0,
            margin: 0,
            marginTop: "16px",
          }}
        >
          <Box sx={{ display: "flex", gap: 2, flexDirection: "column" }}>
            {/* <InputSection label="Tool Choice" icon="solar:info-circle-bold" tooltipText="If your application should call a function"
            >
              <FormSelectField
                control={control}
                fieldName="config.toolChoice"
                size="small"
                onChange={(e) => {
                  if (e.target.value === "none") {
                    setValue("config.tools", []);
                  }
                }}
                options={[
                  ...(runPromptOptions?.tool_choices || []),
                  { value: "none", label: "None" },
                ]}
                sx={{ width: 200 }}
              />
            </InputSection> */}

            {/* <ConfigTool control={control} /> */}
            <Stack direction={"row"} gap={theme.spacing(1)}>
              <FormSearchSelectFieldControl
                sx={{
                  flex: 1,
                }}
                fullWidth
                label="Tool"
                size="small"
                control={control}
                fieldName="config.tools"
                options={toolsOptions}
                checkbox
                multiple
              />
              <Button
                onClick={() => {
                  setEditTool(null);
                  onOpenCustomToolModal();
                }}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  padding: theme.spacing(1, 2),
                }}
                startIcon={
                  <SvgColor
                    sx={{
                      color: "text.secondary",
                      height: 20,
                      width: 20,
                    }}
                    src={"/assets/icons/ic_add.svg"}
                  />
                }
              >
                <Typography
                  variant="s1"
                  color={"text.secondary"}
                  fontWeight={"fontWeightMedium"}
                >
                  Create tool
                </Typography>
              </Button>
            </Stack>
            <Stack direction={"column"} gap={theme.spacing(1.5)}>
              {filteredToolOptions?.map((tool) => (
                <SelectedItemWithActions
                  onEdit={() => {
                    onOpenCustomToolModal();
                    setEditTool(tool?.tool);
                  }}
                  value={tool?.value}
                  onRemove={handleRemoveTool}
                  key={tool?.value}
                  label={tool.label}
                />
              ))}
            </Stack>
          </Box>
        </AccordionDetails>
      </Accordion>
    </>
  );
};

ToolsConfigSection.propTypes = {
  control: PropTypes.object,
  setValue: PropTypes.func,
  tools: PropTypes.array,
  selectedTools: PropTypes.array,
  onOpenCustomToolModal: PropTypes.func,
  setEditTool: PropTypes.func,
};

export default ToolsConfigSection;
