import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import HelperText from "../Common/HelperText";
import { useForm } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

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

const getDefaultValue = (tool) => {
  if (!tool)
    return {
      name: "",
      config: `{
"name": "new_tool",
"description": "",
"parameters": {
  "type": "object",
  "properties": {},
  "required": []
}
}`,
    };

  return {
    name: tool.name,
    config: JSON.stringify(tool.config, null, 2),
  };
};

const AddToolChild = ({ editTool, onClose }) => {
  const isEditMode = Boolean(editTool);

  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValue(editTool),
    resolver: zodResolver(
      z.object({
        name: z.string().min(1, "Name is required"),
        config: z
          .string()
          .min(1, "Config is required")
          .refine((val) => {
            try {
              JSON.parse(val);
              return true;
            } catch {
              return false;
            }
          })
          .transform((val) => JSON.parse(val)),
      }),
    ),
  });

  const onCloseClick = () => {
    reset();
    onClose();
  };

  const queryClient = useQueryClient();

  const { mutate: createTool, isPending: isCreating } = useMutation({
    mutationFn: (data) => axios.post(endpoints.tools.create, data),
    onSuccess: () => {
      enqueueSnackbar("Tool created successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onCloseClick();
    },
  });
  const { mutate: updateTool, isPending: isUpdating } = useMutation({
    mutationFn: (data) => axios.put(endpoints.tools.update(editTool?.id), data),
    onSuccess: () => {
      enqueueSnackbar("Tool updated successfully", { variant: "success" });
      queryClient.invalidateQueries({
        queryKey: ["develop", "run-prompt-options"],
      });
      onCloseClick();
    },
  });

  const onSubmit = (data) => {
    if (isEditMode) {
      updateTool(data);
    } else {
      createTool(data);
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <DialogTitle>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography fontWeight={600} fontSize="16px">
            {isEditMode ? "Edit tool" : "Configure a tool"}
          </Typography>
          <IconButton onClick={onCloseClick}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <HelperText text="Configured tools can be used with OpenAI models that support tool calling (or function calling)." />
        <FormTextFieldV2
          control={control}
          fieldName="name"
          label="Tool name"
          placeholder="Enter tool name"
          sx={{ width: "50%" }}
          autoFocus
        />
        <Box
          sx={{
            paddingY: 2,
            paddingX: 1.5,
            backgroundColor: "background.neutral",
            borderRadius: "8px",
          }}
        >
          <FormCodeEditor
            height="300px"
            defaultLanguage="python"
            options={editorOptions}
            language="json"
            control={control}
            fieldName="config"
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button variant="outlined" onClick={onCloseClick}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="primary"
          type="submit"
          loading={isCreating || isUpdating}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </form>
  );
};

AddToolChild.propTypes = {
  editTool: PropTypes.object,
  onClose: PropTypes.func,
  open: PropTypes.bool,
};

const AddToolModal = (props) => {
  return (
    <Dialog
      open={props.open}
      onClose={props.onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 960 } }}
      fullWidth
    >
      <AddToolChild {...props} />
    </Dialog>
  );
};

AddToolModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  editTool: PropTypes.object,
};

export default AddToolModal;
