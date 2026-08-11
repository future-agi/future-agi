import { zodResolver } from "@hookform/resolvers/zod";
import { LoadingButton } from "@mui/lab";
import { Box, Button, Typography, useTheme } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop/AddDatasetDrawer/AddDatasetStyle";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import SvgColor from "src/components/svg-color";
import axios, { endpoints } from "src/utils/axios";
import { z } from "zod";
import { ShowComponent } from "src/components/show";
import YAML from "yaml";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";
import {
  editorOptions,
  getDefaultValues,
  tabOptions,
} from "./EditTool.utils";

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

const EditTool = ({ onCancel, editTool }) => {
  const isEditMode = Boolean(editTool);
  const queryClient = useQueryClient();
  const theme = useTheme();

  const {
    control,
    handleSubmit,
    watch,
    register,
    setValue,
    reset,
    formState: { isValid },
  } = useForm({
    defaultValues: getDefaultValues(editTool),
    resolver: zodResolver(toolSchema),
  });

  const configType = watch("config_type");

  useEffect(() => {
    if (isEditMode) {
      reset(getDefaultValues(editTool)); // Handles full form population
    } else {
      reset(getDefaultValues(null));
    }
  }, [editTool, reset, isEditMode]);

  const onSubmit = (data) => {
    if (configType === "json") {
      data["config"] = JSON.parse(data?.inputSchema?.json);
    } else {
      data["config"] = data?.inputSchema?.yaml;
    }
    delete data?.inputSchema;

    if (isEditMode) {
      updateTool(data);
    } else {
      createTool(data);
    }
  };

  const { mutate: createTool, isPending: isCreating } = useMutation({
    mutationFn: (data) => axios.post(endpoints.tools.create, data),
    onSuccess: () => {
      enqueueSnackbar("Tool created successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onCancel();
    },
  });
  const { mutate: updateTool, isPending: isUpdating } = useMutation({
    mutationFn: (data) => axios.put(endpoints.tools.update(editTool?.id), data),
    onSuccess: () => {
      enqueueSnackbar("Tool updated successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onCancel();
    },
  });

  return (
    <Box
      sx={{
        padding: "8px",
        borderRadius: "8px",
        backgroundColor: "background.neutral",
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        border: "1px solid",
        borderColor: "divider",
      }}
      component="form"
      onSubmit={(e) => {
        e.stopPropagation();
        e.preventDefault();
        handleSubmit(onSubmit)(e);
      }}
    >
      <Typography
        variant="s2"
        fontWeight={"fontWeightMedium"}
        color="text.primary"
      >
        {editTool?.name || "Custom Tool"}
      </Typography>
      <input type="hidden" {...register("config_type")} />
      <FormTextFieldV2
        control={control}
        fieldName={"name"}
        size="small"
        label={"Name"}
        placeholder="Enter name"
        required
        sx={{ backgroundColor: "background.paper" }}
      />
      <FormTextFieldV2
        control={control}
        fieldName={"description"}
        size="small"
        label={"Description"}
        placeholder="Enter description"
        required
        sx={{ backgroundColor: "background.paper" }}
      />
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
        <Typography
          variant="s2"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Input schema
        </Typography>
        <TabWrapper sx={{ marginBottom: "0", width: "max-content" }}>
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
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.paper",
            padding: (theme) => theme.spacing(1),
            borderRadius: (theme) => theme.spacing(0.5),
          }}
        >
          <ShowComponent condition={configType === "json"}>
            <FormCodeEditor
              helperText={""}
              height="200px"
              defaultLanguage={"json"}
              lannguage={"json"}
              control={control}
              fieldName="inputSchema.json"
              options={editorOptions}
              copyIcon={
                <SvgColor
                  src="/assets/icons/ic_copy.svg"
                  alt="Copy"
                  sx={{ width: "12px", height: "12px" }}
                />
              }
            />
          </ShowComponent>

          <ShowComponent condition={configType === "yaml"}>
            <FormCodeEditor
              helperText={""}
              height="200px"
              defaultLanguage={"yaml"}
              language={"yaml"}
              control={control}
              fieldName="inputSchema.yaml"
              options={editorOptions}
              copyIcon={
                <SvgColor
                  src="/assets/icons/ic_copy.svg"
                  alt="Copy"
                  sx={{ width: "12px", height: "12px" }}
                />
              }
            />
          </ShowComponent>
        </Box>
      </Box>
      <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1.4 }}>
        <Button
          onClick={onCancel}
          variant="outlined"
          type="button"
          sx={{
            width: "90px",
            height: "30px",
            border: "1px solid",
            borderColor: "action.hover",
            backgroundColor: "background.paper",
            color: "text.disabled",
            display: "flex",
            alignItems: "center",
            flexDirection: "row",
            ...theme.typography["s2"],
            fontWeight: (theme) => theme.typography.fontWeightMedium,
            py: (theme) => theme.spacing(0.75),
            px: (theme) => theme.spacing(3),
            borderRadius: "8px",
          }}
        >
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          sx={{
            minWidth: "90px",
            height: "30px",
            ...theme.typography["s2"],
            fontWeight: (theme) => theme.typography["fontWeightSemiBold"],
            py: (theme) => theme.spacing(0.75),
            px: (theme) => theme.spacing(3),
            ...(!isValid && {
              color: (theme) => `${theme.palette.background.paper} !important`,
              backgroundColor: (theme) => `${theme.palette.divider} !important`,
            }),
          }}
          disabled={!isValid}
          loading={isUpdating || isCreating}
        >
          {editTool ? "Update" : "Add tool"}
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default EditTool;

EditTool.propTypes = {
  item: PropTypes.object,
  onCancel: PropTypes.func,
  editTool: PropTypes.object,
};
