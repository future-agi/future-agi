import React, { useEffect, useRef } from "react";
import {
  Box,
  Typography,
  Dialog,
  DialogTitle,
  DialogContent,
  IconButton,
  Chip,
} from "@mui/material";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import Iconify from "../iconify";
import { FormCodeEditor } from "../form-code-editor";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { LoadingButton } from "@mui/lab";
import { editorOptions, validateNonEmptyJsonObject } from "./KeysHelper";

const CloudProviderModals = ({ provider, onClose, onSubmit }) => {
  const hasClearedOnceRef = useRef(false);
  const schema = z
    .object({
      key: z.string().min(1, "A valid JSON is required"),
    })
    .superRefine((data, ctx) => {
      if (!validateNonEmptyJsonObject(data.key)) {
        ctx.addIssue({
          path: ["key"],
          code: z.ZodIssueCode.custom,
          message: "Must be a valid non-empty JSON object",
        });
      }
    });
  const {
    control,
    formState: { isDirty },
    handleSubmit,
    setValue,
    reset,
  } = useForm({
    defaultValues: {
      key: JSON.stringify(provider?.maskedKey ?? {}, null, 2),
    },
    resolver: zodResolver(schema),
  });

  useEffect(() => {
    if (!provider) return;
    reset({
      key:
        provider?.maskedKey && typeof provider.maskedKey === "object"
          ? JSON.stringify(provider.maskedKey, null, 2)
          : JSON.stringify({}, null, 2),
    });
  }, [provider, reset]);

  // const onSubmit = (data) => {
  //   if (isJson) {
  //     try {
  //       JSON.parse(data.key);
  //       clearErrors("key");
  //     } catch (err) {
  //       setError("key", {
  //         type: "manual",
  //         message: "Invalid JSON format",
  //       });
  //       return;
  //     }
  //   }

  //   trackEvent(Events.saveApiClicked, {
  //     [PropertyName.click]: data.provider,
  //   });
  //   createOrUpdateKey(data);
  // };

  const handleFormSubmit = (data) => {
    onSubmit?.(data);
    onClose();
  };

  const onFocusInput = () => {
    if (hasClearedOnceRef.current) return;
    setValue("key", "", { shouldDirty: true });
    hasClearedOnceRef.current = true;
    // trackEvent(Events.apiKeyBoxClicked);
  };

  return (
    <Dialog
      open={Boolean(provider)}
      onClose={onClose}
      PaperProps={{
        sx: {
          minWidth: "720px",
          borderRadius: "8px",
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        },
      }}
    >
      <DialogTitle sx={{ padding: 0 }}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightSemiBold"}
        >
          Configure {provider?.display_name}
        </Typography>
      </DialogTitle>
      <IconButton
        onClick={onClose}
        sx={{
          position: "absolute",
          top: "12px",
          right: "12px",
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <DialogContent sx={{ padding: 0 }}>
        <Box py={1} display={"flex"} flexDirection={"column"} gap={1}>
          {/* Conditional Field: Text or JSON */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              e.stopPropagation();
              handleSubmit(handleFormSubmit)();
            }}
          >
            <>
              <Chip
                label={"JSON"}
                sx={{
                  marginBottom: 1,
                  width: "max-content",
                  backgroundColor: "action.hover",
                  borderBottomLeftRadius: "4px",
                  color: "primary.main",
                  "& .MuiChip-label": {
                    padding: 0,
                  },
                  "&:hover": {
                    backgroundColor: "action.hover",
                    color: "primary.main",
                  },
                }}
              />
              <FormCodeEditor
                height="250px"
                defaultLanguage="json"
                options={{
                  ...editorOptions,
                  lineNumbers: "off",
                  lineDecorationsWidth: 0,
                  lineNumbersMinChars: 2,
                  glyphMargin: false,
                }}
                theme="xcode-default"
                language="json"
                control={control}
                fieldName="key"
                className="json-editor"
                showError={true}
                onFocusInput={onFocusInput}
                helperText={undefined}
              />
            </>

            <Box sx={{ display: "flex", justifyContent: "flex-end", mt: 2 }}>
              <LoadingButton
                variant="contained"
                color="primary"
                type="submit"
                disabled={!isDirty}
                sx={{
                  minWidth: "200px",
                }}
              >
                Save
              </LoadingButton>
            </Box>
          </form>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

CloudProviderModals.propTypes = {
  provider: PropTypes.object,
  onClose: PropTypes.func,
  onSubmit: PropTypes.func,
};

export default CloudProviderModals;
