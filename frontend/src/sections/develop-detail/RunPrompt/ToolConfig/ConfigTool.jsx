import {
  Box,
  Button,
  FormHelperText,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { useFieldArray, useFormState, useWatch } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import CustomToolModal from "../Modals/CustomToolModal";
import SvgColor from "src/components/svg-color";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import SelectedItemWithActions from "src/components/SelectedItemWithActions";

const ConfigTool = ({ control, fieldName = "config.tools" }) => {
  const [isAddToolModalOpen, setIsAddToolModalOpen] = useState(false);
  const { data: runPromptOptions } = useRunPromptOptions();
  const theme = useTheme();

  const isDisabled =
    useWatch({ control, name: "config.toolChoice" }) === "none";

  const watchedTools = useWatch({
    control,
    name: fieldName,
  });

  const [selectedTool, setSelectedTool] = useState(null);

  const { fields, remove, replace } = useFieldArray({
    control,
    name: fieldName,
  });

  const { errors } = useFormState({ control });

  const errorMessage = errors?.config?.tools?.message;

  const toolsOptions = useMemo(() => {
    return (
      runPromptOptions?.available_tools?.map((t) => ({
        label: t.name,
        value: t.id,
        tool: t,
      })) ?? []
    );
  }, [runPromptOptions?.available_tools]);

  const isToolsPresent = toolsOptions?.length > 0;

  const renderContent = () => {
    if (!isToolsPresent) {
      return (
        <Button
          disabled={isDisabled}
          size="small"
          variant="outlined"
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
          onClick={() => setIsAddToolModalOpen(true)}
          sx={{
            "& .MuiButton-icon > svg": { marginRight: 0 },
            minWidth: "unset",
            height: 33,
            ml: "auto",
          }}
        >
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Create tool
          </Typography>
        </Button>
      );
    }

    return (
      <Box
        sx={{ display: "flex", gap: 1, alignItems: "center", width: "100%" }}
      >
        <FormSearchSelectFieldState
          size="small"
          disabled={isDisabled}
          label="Select a tool"
          options={toolsOptions}
          placeholder="Select a tool"
          multiple
          checkbox
          value={watchedTools?.map((t) => t?.tool?.value) || []}
          sx={{ flex: 1 }}
          onChange={(e) => {
            const selectedValues = e.target.value; // array of selected ids
            const selectedTools = toolsOptions.filter((tool) =>
              selectedValues.includes(tool.value),
            );
            const newFields = selectedTools.map((tool) => ({
              id: getRandomId(),
              tool,
            }));
            replace(newFields); // replace the entire field array
          }}
        />

        <Button
          disabled={isDisabled}
          size="medium"
          variant="outlined"
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
          onClick={() => setIsAddToolModalOpen(true)}
          sx={{
            "& .MuiButton-icon > svg": { marginRight: 0 },
            minWidth: "unset",
            borderColor: "divider",
            height: 40,
            ml: "auto",
          }}
        >
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Create tool
          </Typography>
        </Button>
      </Box>
    );
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          flexDirection: "column",
          gap: (theme) => theme.spacing(2),
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography
            variant="s3"
            fontWeight={"fontWeightMedium"}
            color={"text.secondary"}
          >
            Tool Config
          </Typography>
          <Tooltip title="Define the config for your function" arrow>
            <SvgColor
              sx={{
                height: "16px",
                width: "16px",
                color: "text.primary",
              }}
              src="/assets/icons/ic_info.svg"
            />
          </Tooltip>
        </Box>
        {renderContent()}
      </Box>
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        <Stack
          direction={"column"}
          gap={theme.spacing(1.5)}
          sx={{ width: "100%" }}
        >
          {fields?.map((tool, index) => (
            <SelectedItemWithActions
              sx={{ width: "100%" }}
              onEdit={() => {
                const fTool = toolsOptions?.find(
                  (t) => t.value === tool?.tool?.value,
                );
                setSelectedTool(fTool?.tool);
              }}
              value={tool?.tool?.value}
              onRemove={() => remove(index)}
              key={tool?.id}
              label={
                toolsOptions?.find((t) => t.value === tool?.tool?.value)?.label
              }
            />
          ))}
        </Stack>
      </Box>
      {errorMessage && <FormHelperText error>{errorMessage}</FormHelperText>}

      <CustomToolModal
        editTool={selectedTool}
        setEditTool={setSelectedTool}
        open={isAddToolModalOpen || Boolean(selectedTool)}
        onClose={() => {
          setIsAddToolModalOpen(false);
          setSelectedTool(null);
        }}
      />
    </Box>
  );
};

ConfigTool.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
};

export default ConfigTool;
