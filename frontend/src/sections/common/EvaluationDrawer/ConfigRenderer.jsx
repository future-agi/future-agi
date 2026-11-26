import {
  Box,
  Button,
  IconButton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { get } from "lodash";
import { useFieldArray, useFormState } from "react-hook-form";
import { editorOptions } from "src/components/custom-model-dropdown/KeysHelper";
import { FormCodeEditor } from "src/components/form-code-editor";
import { FormCheckboxField } from "src/components/FormCheckboxField";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";

const ListConfigRenderer = ({
  control,
  fieldName,
  label,
  error,
  viewMode,
  description,
}) => {
  const theme = useTheme();
  const [keyword, setKeyword] = useState("");
  const { fields, append, remove } = useFieldArray({
    control: control,
    name: fieldName,
  });
  const handleAddKeyword = () => {
    append({ value: keyword });
    setKeyword("");
  };

  const handleRemoveKeyword = (index) => {
    remove(index);
  };

  return (
    <Box
      display={"flex"}
      justifyContent={"space-between"}
      flexDirection={"row"}
      gap={theme.spacing(1.5)}
      alignItems={"flex-start"}
    >
      <Box
        display={"flex"}
        flexDirection={"column"}
        width={"100%"}
        gap={2}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <TextField
          value={keyword}
          fullWidth
          size="small"
          label={label}
          disabled={viewMode}
          error={!!error}
          required
          placeholder="Type a choice and press enter"
          onChange={(e) => {
            setKeyword(e.target.value);
          }}
          helperText={error?.message ?? description}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAddKeyword();
            }
          }}
          sx={{
            "& .MuiFormHelperText-root": {
              marginLeft: 0,
            },
          }}
        />
        <ShowComponent condition={fields.length > 0}>
          <Box
            display={"flex"}
            flexDirection={"row"}
            alignSelf={"flex-start"}
            gap={1}
            flexWrap={"wrap"}
          >
            {fields.map((item, index) => (
              <Box
                key={index}
                bgcolor={"action.hover"}
                display={"flex"}
                alignItems={"center"}
                flexDirection={"row"}
                flexShrink={0}
                py={0}
                px={0.5}
                borderRadius="4px"
              >
                <Typography
                  typography={"s2"}
                  sx={{
                    color: "primary.main",
                    fontWeight: "fontWeightMedium",
                    borderRadius: theme.spacing(0.5),
                    paddingY: theme.spacing(0.5),
                    paddingX: theme.spacing(1),
                  }}
                >
                  {item["value"] ?? ""}
                </Typography>
                <IconButton
                  onClick={() => handleRemoveKeyword(index)}
                  disabled={viewMode}
                  sx={{
                    bgcolor: "action.hover",
                    ":hover": {
                      bgcolor: "action.hover",
                    },
                  }}
                >
                  <SvgColor
                    src="/assets/icons/ic_close.svg"
                    sx={{
                      height: 12,
                      width: 12,
                      color: "common.white",
                    }}
                  />
                </IconButton>
              </Box>
            ))}
          </Box>
        </ShowComponent>
      </Box>
      <Button
        variant="outlined"
        color="primary"
        disabled={viewMode}
        onClick={handleAddKeyword}
        sx={{
          color: "primary.main",
          "& .MuiButton-startIcon": {
            margin: 0,
            paddingRight: theme.spacing(1),
          },
        }}
        startIcon={
          <SvgColor
            width={20}
            src="/assets/icons/action_buttons/ic_add.svg"
            sx={{
              color: "primary.main",
            }}
          />
        }
      >
        <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
          Add
        </Typography>
      </Button>
    </Box>
  );
};

ListConfigRenderer.propTypes = {
  control: PropTypes.object,
  error: PropTypes.object,
  viewMode: PropTypes.bool,
  fieldName: PropTypes.string,
  label: PropTypes.string,
  description: PropTypes.string,
};

const ConfigRenderer = ({
  control,
  fieldName,
  label,
  type,
  description,
  viewMode,
  required = true,
}) => {
  const theme = useTheme();
  const { errors } = useFormState({ control: control });
  const error = get(errors, fieldName);

  switch (type) {
    case "integer":
      return (
        <FormTextFieldV2
          control={control}
          placeholder={`Enter ${label}`}
          fieldName={fieldName}
          label={label}
          disabled={viewMode}
          required={required}
          helperText={description}
          size="small"
          fieldType="number"
        />
      );
    case "string":
      return (
        <FormTextFieldV2
          control={control}
          placeholder={`Enter ${label}`}
          fieldName={fieldName}
          size="small"
          required={required}
          helperText={description}
          label={label}
          disabled={viewMode}
        />
      );
    case "boolean":
      return (
        <FormCheckboxField
          control={control}
          // disabled={isViewMode}
          label={label}
          fieldName={fieldName}
          helperText={description}
          disabled={viewMode}
          defaultValue={false}
          labelProps={{
            gap: theme.spacing(1),
          }}
          checkboxSx={{
            padding: 0,
            "&.Mui-checked": {
              color: "primary.light",
            },
          }}
          labelPlacement="end"
        />
      );
    case "code":
      return (
        <FormCodeEditor
          control={control}
          label={"Code to be executed"}
          fieldName={fieldName}
          height="250px"
          options={{
            lineNumbers: "off",
            lineDecorationsWidth: 0,
            lineNumbersMinChars: 2,
            glyphMargin: false,
          }}
          readOnly={viewMode}
          required={required}
          editorOptions={editorOptions}
          language="python"
          defaultValue={"# Python code to be executed"}
          onFocusInput={() => {}}
          helperText={description}
        />
      );
    case "dict":
      return (
        <FormCodeEditor
          helperText={description}
          height="200px"
          defaultLanguage={"json"}
          language={"json"}
          control={control}
          options={editorOptions}
          label={label}
          readOnly={viewMode}
          fieldName={fieldName}
          className="json-editor"
          showError={true}
          onFocusInput={() => {}}
          sx={{ width: "100%" }}
        />
      );
    case "list":
      return (
        <ListConfigRenderer
          error={error}
          control={control}
          description={description}
          fieldName={fieldName}
          label={label}
          viewMode={viewMode}
        />
      );
    default:
      return (
        <FormTextFieldV2
          control={control}
          fieldName={fieldName}
          label={label}
          disabled={viewMode}
          required={required}
          size="small"
        />
      );
  }
};

export default ConfigRenderer;

ConfigRenderer.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  label: PropTypes.string,
  type: PropTypes.string,
  viewMode: PropTypes.bool,
  description: PropTypes.string,
  required: PropTypes.bool,
};
