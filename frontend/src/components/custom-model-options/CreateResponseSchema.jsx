import React, { useMemo } from "react";
import { SegmentedTabs } from "src/components/tabs/tabs";
import PromptModalWrapper from "./PromptModalWrapper";
import {
  Box,
  DialogContent,
  Stack,
  Typography,
  useTheme,
  Tab,
} from "@mui/material";
import { useForm, useWatch } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "../svg-color";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";

const tabOptions = [
  { label: "JSON", value: "json", disabled: false },
  { label: "YAML", value: "yaml", disabled: false },
];

const DEFAULT_SCHEMA = {
  json: `{
  "type": "object",
  "properties": {
    "summary": {
      "type": "string",
      "description": "The generated summary."
    }
  },
  "required": ["summary"],
  "additionalProperties": false
}
  `,
  yaml: `type: object
properties:
  summary:
    type: string
    description: "The generated summary."
required:
  - summary
additionalProperties: false
  `,
};

const baseSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    description: z.string().min(1, "Description is required"),
    schema_type: z.enum(["json", "yaml"]),
    schema: z.object({
      json: z.string().optional(),
      yaml: z.string().optional(),
    }),
  })
  .superRefine((data, ctx) => {
    const isJson = data.schema_type === "json";
    const jsonValue = data.schema.json?.trim();
    const yamlValue = data.schema.yaml?.trim();

    // JSON validation
    if (isJson && !jsonValue) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "JSON schema is required",
        path: ["schema", "json"],
      });
    }

    // YAML validation
    if (!isJson && !yamlValue) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "YAML schema is required",
        path: ["schema", "yaml"],
      });
    }
  });

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  wordWrap: "on",
  lineNumbers: "off",
  folding: false,
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

export default function CreateResponseSchema({ open, onClose, setValue }) {
  const theme = useTheme();
  const queryClient = useQueryClient();

  const {
    control,
    handleSubmit,
    reset,
    setValue: setFormValue,
    formState: { isValid },
  } = useForm({
    resolver: zodResolver(baseSchema),
    mode: "onChange",
    reValidateMode: "onChange",
    defaultValues: {
      name: "",
      description: "",
      schema_type: "json",
      schema: {
        json: DEFAULT_SCHEMA["json"],
        yaml: DEFAULT_SCHEMA["yaml"],
      },
    },
  });

  // Watch the schema_type field to control the tab
  const schemaType = useWatch({
    control,
    name: "schema_type",
    defaultValue: "json",
  });

  const onSubmit = (data) => {
    // Validate that the appropriate schema field is filled
    if (data.schema_type === "json") {
      if (!data.schema.json || data.schema.json.trim() === "") {
        return; // Validation will handle this
      }
      try {
        data["schema"] = JSON.parse(data.schema.json);
      } catch (error) {
        return;
      }
    } else {
      if (!data.schema.yaml || data.schema.yaml.trim() === "") {
        return; // Validation will handle this
      }
      data["schema"] = data.schema.yaml;
    }
    mutate(data);
  };

  const { mutate, isPending } = useMutation({
    mutationFn: (data) => {
      return axios.post(endpoints.develop.runPrompt.responseSchema, data);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: ["response-schema"],
      });
      setValue(data?.data.id);
      if (onClose) {
        onClose();
      }
      reset();
    },
  });

  // Check if the current schema field is filled for custom validation
  const currentSchemaField = useWatch({
    control,
    name: schemaType === "json" ? "schema.json" : "schema.yaml",
  });

  const isFormValid = useMemo(() => {
    const hasRequiredFields = isValid;
    const hasSchemaContent =
      currentSchemaField && currentSchemaField.trim() !== "";
    return hasRequiredFields && hasSchemaContent;
  }, [isValid, currentSchemaField]);

  return (
    <PromptModalWrapper
      onSubmit={handleSubmit(onSubmit)}
      isValid={isFormValid}
      actionBtnTitle="Save"
      hideCancelBtn
      open={open}
      onClose={() => {
        onClose();
        reset();
      }}
      title="Create new schema"
      subTitle="Define the JSON schema for the structured output of the prompt"
      isLoading={isPending}
    >
      <DialogContent sx={{ padding: "0", margin: 0, marginTop: "-4px" }}>
        <form
          onSubmit={handleSubmit(onSubmit)}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            paddingTop: "4px",
          }}
        >
          <FormTextFieldV2
            fullWidth
            label={"Name"}
            fieldName={"name"}
            control={control}
            required
            placeholder="Enter name"
            size="small"
          />
          <FormTextFieldV2
            fullWidth
            required
            label={"Description"}
            fieldName={"description"}
            control={control}
            placeholder="Enter description"
            size="small"
          />
          <Stack direction={"column"} gap={"4px"}>
            <Typography
              variant="s2"
              fontWeight={"fontWeightSemiBold"}
              color={"text.primary"}
            >
              Schema definition
            </Typography>
            <Box sx={{ width: "fit-content" }}>
              <Box sx={{ mb: 1.5 }}>
                <SegmentedTabs
                  value={schemaType}
                  onChange={(e, value) => setFormValue("schema_type", value)}
                >
                  {tabOptions.map((tab) => (
                    <Tab
                      key={tab.value}
                      label={tab.label}
                      value={tab.value}
                      disabled={tab.disabled}
                    />
                  ))}
                </SegmentedTabs>
              </Box>
            </Box>
            <ShowComponent condition={schemaType === "json"}>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  padding: theme.spacing(1),
                  borderRadius: theme.spacing(0.5),
                }}
              >
                <FormCodeEditor
                  helperText={""}
                  height="200px"
                  defaultLanguage={"json"}
                  lannguage={schemaType}
                  control={control}
                  placeholder="Enter your json schema here"
                  fieldName="schema.json"
                  options={editorOptions}
                  copyIcon={
                    <SvgColor
                      src="/assets/icons/ic_copy.svg"
                      alt="Copy"
                      sx={{ width: "12px", height: "12px" }}
                    />
                  }
                />
              </Box>
            </ShowComponent>
            <ShowComponent condition={schemaType === "yaml"}>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  padding: theme.spacing(1),
                  borderRadius: theme.spacing(0.5),
                }}
              >
                <FormCodeEditor
                  helperText={""}
                  height="200px"
                  defaultLanguage={"yaml"}
                  control={control}
                  fieldName="schema.yaml"
                  options={editorOptions}
                  placeholder="Enter your yaml schema here"
                  copyIcon={
                    <SvgColor
                      src="/assets/icons/ic_copy.svg"
                      alt="Copy"
                      sx={{ width: "12px", height: "12px" }}
                    />
                  }
                />
              </Box>
            </ShowComponent>
          </Stack>
        </form>
      </DialogContent>
    </PromptModalWrapper>
  );
}

CreateResponseSchema.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  setValue: PropTypes.func,
};
