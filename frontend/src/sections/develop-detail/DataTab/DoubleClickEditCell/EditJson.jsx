import { zodResolver } from "@hookform/resolvers/zod";
import { LoadingButton } from "@mui/lab";
import { Box, DialogActions } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { useForm } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import { z } from "zod";
import ErrorMessage from "./ErrorMessage";
import FileUploader from "./FileUploader";

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: {
    enabled: false,
  },
};

const EditJson = ({ params, onClose, onCellValueChanged }) => {
  const {
    control,
    reset,
    handleSubmit,
    formState: { errors },
    setValue,
  } = useForm({
    defaultValues: {
      jsonData: "",
    },
    resolver: zodResolver(
      z.object({
        jsonData: z
          .string()
          .min(1, "JSON field is required")
          .refine(
            (val) => {
              try {
                JSON.parse(val);
                return true;
              } catch {
                return false;
              }
            },
            {
              message:
                "The JSON provided is incorrect. Please verify their structure, format, and content before resubmitting.",
            },
          )
          .transform((val) => {
            try {
              return JSON.parse(val);
            } catch {
              return val; // Return the original string if parsing fails
            }
          }),
      }),
    ),
  });

  useEffect(() => {
    if (params?.value) {
      setValue("jsonData", params?.value);
    }
  }, [params?.value, setValue]);

  const handleClose = () => {
    onClose();
    reset();
  };

  const onSubmit = (formData) => {
    onCellValueChanged({ ...params, newValue: formData?.jsonData });
    handleClose();
  };

  return (
    <Box
      sx={{
        paddingX: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
        marginTop: "-2px",
        width: "553px",
      }}
      component="form"
      onSubmit={handleSubmit(onSubmit)}
    >
      <Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <FileUploader setValue={setValue} />
          <FormCodeEditor
            height="250px"
            defaultLanguage="python"
            options={{
              ...editorOptions,
              lineNumbers: "on", // Keeps line numbers visible
              lineDecorationsWidth: 0, // Adjust the width of decorations (left sidebar)
              lineNumbersMinChars: 2, // Adds space between the numbers and the text
              glyphMargin: false, // Disables extra margin (if not needed for debugging icons)
            }}
            theme="xcode-default"
            language="json"
            control={control}
            fieldName="jsonData"
            className="json-editor"
            showError={false}
          />
        </Box>
        {errors?.jsonData && (
          <ErrorMessage
            isError={Boolean(errors?.jsonData)}
            errorMessage={errors?.jsonData?.message}
          />
        )}
      </Box>
      <DialogActions
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          marginRight: "-20px",
          marginTop: "-15px",
        }}
      >
        <LoadingButton
          variant="outlined"
          size="small"
          onClick={handleClose}
          sx={{
            width: "90px",
            fontsize: "14px",
            marginRight: "-5px",
          }}
        >
          Cancel
        </LoadingButton>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          // fullWidth
          size="small"
          loading={false}
          sx={{ width: "90px" }}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Box>
  );
};

export default EditJson;

EditJson.propTypes = {
  gridApiRef: PropTypes.object,
  params: PropTypes.object,
  onClose: PropTypes.func,
  onCellValueChanged: PropTypes.func,
};
