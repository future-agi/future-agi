import React, { useEffect } from "react";
import PromptModalWrapper from "./PromptModalWrapper";
import { Box, DialogContent, Stack, Typography, useTheme } from "@mui/material";
import { useForm } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import PropTypes from "prop-types";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import { ShowComponent } from "src/components/show";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import YAML from "yaml";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const tabOptions = [
  { label: "JSON", value: "json", disabled: false },
  { label: "YAML", value: "yaml", disabled: false },
];

function getDefaultValues(editTool) {
  const defaultJson = `{
  "parameters": {
    "type": "object",
    "properties": {},
    "required": []
  }
}`;

  const defaultYaml = `parameters:
  type: object
  properties: {}
  required: []`;

  if (editTool) {
    return {
      name: editTool?.name || "",
      description: editTool?.description || "",
      config_type: editTool?.config_type,
      inputSchema: {
        json:
          editTool?.config_type === "json"
            ? JSON.stringify(editTool?.config, null, 2)
            : defaultJson,
        yaml:
          editTool?.config_type === "yaml"
            ? editTool?.yaml_config
            : defaultYaml,
      },
    };
  }

  return {
    name: "",
    description: "",
    config_type: "json",
    inputSchema: {
      json: defaultJson,
      yaml: defaultYaml,
    },
  };
}

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  wordWrap: "on",
  lineNumbers: "on",
  folding: true,
  minimap: { enabled: false },
  glyphMargin: false,
  lineDecorationsWidth: 0,
  renderIndentGuides: false,
  lineNumbersMinChars: 0,
  scrollbar: {
    vertical: "visible",
    horizontal: "visible",
    verticalScrollbarSize: 10,
    horizontalScrollbarSize: 10,
    alwaysConsumeMouseWheel: false,
    useShadows: false,
  },
};

const toolSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    description: z.string().min(1, "Description is required"),
    config_type: z.enum(["json", "yaml"]),
    inputSchema: z.object({
      json: z.string().optional(),
      yaml: z.string().optional(),
    }),
  })
  .superRefine((data, ctx) => {
    const { config_type, inputSchema } = data;
    if (config_type === "json") {
      if (!inputSchema.json?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "JSON input schema is required",
          path: ["inputSchema", "json"],
        });
      } else {
        try {
          JSON.parse(inputSchema.json);
        } catch (err) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Invalid JSON syntax",
            path: ["inputSchema", "json"],
          });
        }
      }
    }

    if (config_type === "yaml") {
      if (!inputSchema.yaml?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "YAML input schema is required",
          path: ["inputSchema", "yaml"],
        });
      } else {
        try {
          YAML.parse(inputSchema.yaml);
        } catch (err) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Invalid YAML syntax",
            path: ["inputSchema", "yaml"],
          });
        }
      }
    }
  });

export default function CustomToolModal({
  open,
  onClose,
  editTool,
  setEditTool,
}) {
  const {
    control,
    handleSubmit,
    reset,
    formState: { isValid },
    register,
    watch,
    setValue,
  } = useForm({
    resolver: zodResolver(toolSchema),
    defaultValues: getDefaultValues(editTool),
  });
  const theme = useTheme();
  const configType = watch("config_type");

  useEffect(() => {
    if (editTool) {
      reset(getDefaultValues(editTool)); // Handles full form population
    } else {
      reset(getDefaultValues(null));
    }
  }, [editTool, reset]);

  const queryClient = useQueryClient();

  const { mutate: createTool, isPending: isCreatingTool } = useMutation({
    mutationFn: (data) => axios.post(endpoints.tools.create, data),
    onSuccess: () => {
      enqueueSnackbar("Tool created successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onClose();
    },
  });

  const { mutate: updateTool, isPending: isUpdatingTool } = useMutation({
    mutationFn: (data) => axios.put(endpoints.tools.update(editTool?.id), data),
    onSuccess: () => {
      enqueueSnackbar("Tool updated successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onClose();
      setTimeout(() => {
        setEditTool(null);
      }, 0);
    },
  });

  const onSubmit = (data) => {
    if (configType === "json") {
      data["config"] = JSON.parse(data?.inputSchema?.json);
    } else {
      data["config"] = data?.inputSchema?.yaml;
    }
    delete data?.inputSchema;
    if (editTool?.id) {
      updateTool(data);
    } else {
      createTool(data);
    }
  };
  return (
    <PromptModalWrapper
      isLoading={isCreatingTool || isUpdatingTool}
      onSubmit={handleSubmit(onSubmit)}
      isValid={isValid}
      actionBtnTitle={editTool ? "Update tool" : "Add tool"}
      open={open}
      onClose={() => {
        onClose();
        reset();
      }}
      title="Custom tool"
    >
      <DialogContent
        sx={{
          padding: "0",
        }}
      >
        <form
          onSubmit={handleSubmit(onSubmit)}
          style={{
            paddingTop: "4px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          <input type="hidden" {...register("config_type")} />
          <FormTextFieldV2
            fullWidth
            required
            label={"Name"}
            fieldName={"name"}
            control={control}
            placeholder="Enter name"
            size="small"
            sx={{
              pt: "4px",
              "& .MuiInputLabel-root": {
                backgroundColor: "transparent !important",
              },
            }}
          />
          <FormTextFieldV2
            fullWidth
            label={"Description"}
            fieldName={"description"}
            control={control}
            placeholder="Enter description"
            size="small"
            required
            sx={{
              pt: "4px",
              "& .MuiInputLabel-root": {
                backgroundColor: "transparent !important",
              },
            }}
          />
          <Stack direction={"column"} gap={"4px"}>
            <Typography
              variant="s1"
              fontWeight={"fontWeightRegular"}
              color={"text.primary"}
            >
              Input Schema
              <Box component="span" sx={{ color: "error.main" }}>
                {" *"}
              </Box>
            </Typography>
            <Box
              sx={{
                width: "fit-content",
              }}
            >
              <TabWrapper
                sx={{
                  marginBottom: "0",
                }}
              >
                <CustomTabs
                  textColor="primary"
                  value={configType}
                  onChange={(e, value) => setValue("config_type", value)}
                  TabIndicatorProps={{
                    style: {
                      backgroundColor: theme.palette.primary.main,
                      opacity: 0.08,
                      height: "100%",
                      borderRadius: "8px",
                    },
                  }}
                >
                  {tabOptions.map((tab) => (
                    <CustomTab
                      key={tab.value}
                      label={tab.label}
                      value={tab.value}
                      disabled={tab.disabled}
                    />
                  ))}
                </CustomTabs>
              </TabWrapper>
            </Box>
            <ShowComponent condition={configType === "json"}>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  padding: theme.spacing(1),
                  borderRadius: theme.spacing(0.5),
                }}
              >
                <FormCodeEditor
                  height="200px"
                  defaultLanguage={"json"}
                  lannguage={"json"}
                  control={control}
                  fieldName="inputSchema.json"
                  options={editorOptions}
                />
              </Box>
            </ShowComponent>
            <ShowComponent condition={configType === "yaml"}>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  padding: theme.spacing(1),
                  borderRadius: theme.spacing(0.5),
                }}
              >
                <FormCodeEditor
                  height="200px"
                  defaultLanguage={"yaml"}
                  language={"yaml"}
                  control={control}
                  fieldName="inputSchema.yaml"
                  options={editorOptions}
                />
              </Box>
            </ShowComponent>
          </Stack>
        </form>
      </DialogContent>
    </PromptModalWrapper>
  );
}

CustomToolModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  handleSubmitTool: PropTypes.func,
  isLoading: PropTypes.bool,
  editTool: PropTypes.object,
  setEditTool: PropTypes.func,
};
