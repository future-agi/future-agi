import {
  Box,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import React, { useState, useEffect, useTransition } from "react";
import { useFormContext, Controller } from "react-hook-form";
import { useGlobalVariablesDrawerStoreShallow } from "../../store";
import PropTypes from "prop-types";
import EmptyVariable from "src/components/VariableDrawer/EmptyVariable";
import {
  escapeModelKey,
  unescapeModelKey,
} from "src/sections/develop-detail/Experiment/utils";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import { useUpdateDatasetCell } from "../../../../api/agent-playground/agent-playground";
import useWorkflowExecution from "../../hooks/useWorkflowExecution";

function FormField({ label, value, onChange }) {
  return (
    <Stack
      direction={"column"}
      gap={0}
      sx={{
        border: "1px solid",
        borderColor: "whiteScale.500",
        borderRadius: (theme) => theme.spacing(0.5, 0.5, 0, 0),
      }}
    >
      <Box
        sx={{
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? "background.paper"
              : "whiteScale.200",
          padding: 1,
          borderBottom: "1px solid",
          borderColor: "whiteScale.500",
        }}
      >
        <Typography
          color={"text.primary"}
          typography={"s2_1"}
          fontWeight={"fontWeightMedium"}
        >{`{{${label}}}`}</Typography>
      </Box>
      <TextField
        size="small"
        variant="outlined"
        sx={{
          "& .MuiOutlinedInput-notchedOutline": {
            border: "none",
          },
        }}
        value={value}
        onChange={onChange}
      />
    </Stack>
  );
}

FormField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

export default function ManualVariablesForm({
  formValues,
  isDirty,
  cellMap,
  graphId,
  variables = {},
}) {
  const { setGlobalVariables, setOpen, pendingRun, setPendingRun } =
    useGlobalVariablesDrawerStoreShallow((s) => ({
      setGlobalVariables: s.setGlobalVariables,
      setOpen: s.setOpen,
      pendingRun: s.pendingRun,
      setPendingRun: s.setPendingRun,
    }));

  const { control, reset } = useFormContext();
  const { mutateAsync: updateCell } = useUpdateDatasetCell();
  const { runWorkflow } = useWorkflowExecution();
  const [isSaving, setIsSaving] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [, startTransition] = useTransition();

  useEffect(() => {
    startTransition(() => setIsReady(true));
    return () => setIsReady(false);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    if (!cellMap || !graphId) return;

    setIsSaving(true);
    try {
      // Unescape dotted keys from RHF form values back to flat store shape
      const flatValues = {};
      for (const [escapedKey, value] of Object.entries(formValues)) {
        flatValues[unescapeModelKey(escapedKey)] = value;
      }

      // Find changed fields and update each cell via cellMap lookup
      const updates = [];
      for (const [key, value] of Object.entries(flatValues)) {
        if (value === variables[key]) continue;
        const cell = cellMap[key];
        if (cell) {
          updates.push(updateCell({ graphId, cellId: cell.id, value }));
        }
      }

      await Promise.all(updates);
      setGlobalVariables(flatValues);
      reset(formValues);
      enqueueSnackbar("Variables saved successfully", { variant: "success" });
      setOpen(false);
      if (pendingRun) {
        setPendingRun(false);
        runWorkflow();
      }
    } catch {
      enqueueSnackbar("Failed to save variables", { variant: "error" });
    } finally {
      setIsSaving(false);
    }
  };

  const isEmpty = Object.keys(variables).length === 0;
  if (isEmpty) {
    return (
      <Box sx={{ height: "calc(100vh - 100px)", width: "100%" }}>
        <EmptyVariable />
      </Box>
    );
  }

  return (
    <Stack gap={2}>
      {!isReady ? (
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            height: 200,
          }}
        >
          <CircularProgress size={24} />
        </Box>
      ) : (
        <Box>
          <Stack direction="column" spacing={2}>
            {Object.keys(variables).map((key) => (
              <Controller
                key={key}
                name={escapeModelKey(key)}
                control={control}
                render={({ field }) => (
                  <FormField
                    label={key}
                    value={field.value}
                    onChange={field.onChange}
                  />
                )}
              />
            ))}
          </Stack>
        </Box>
      )}
      {isReady && (
        <Box
          sx={{
            ml: "auto",
            bgcolor: "background.paper",
            width: "100%",
            display: "flex",
            justifyContent: "flex-end",
            gap: 1,
            flexShrink: 0,
            position: "sticky",
            bottom: 0,
            py: 1,
          }}
        >
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            onClick={handleSave}
            loading={isSaving}
            disabled={!isDirty || isSaving}
          >
            {pendingRun ? "Save & Run Workflow" : "Save"}
          </LoadingButton>
        </Box>
      )}
    </Stack>
  );
}

ManualVariablesForm.propTypes = {
  formValues: PropTypes.object.isRequired,
  isDirty: PropTypes.bool.isRequired,
  cellMap: PropTypes.object,
  graphId: PropTypes.string,
  variables: PropTypes.object.isRequired,
};
