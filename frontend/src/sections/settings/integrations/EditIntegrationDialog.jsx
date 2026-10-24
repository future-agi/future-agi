import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import z from "zod";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useUpdateConnection } from "src/api/integrations";
import { SYNC_INTERVALS } from "./constants";
import { outlinedNeutralButtonSx } from "./styles";
import { getErrorMessage } from "./utils";

const editSchema = z.object({
  display_name: z.string().optional(),
  host_url: z
    .string()
    .optional()
    .refine((val) => !val || /^https?:\/\/.+/.test(val), {
      message: "Must be a valid URL",
    }),
  public_key: z.string().optional(),
  secret_key: z.string().optional(),
  sync_interval_seconds: z.number(),
});

function EditDialogContent({ onClose, connection }) {
  const [showSecret, setShowSecret] = useState(false);
  const { mutate: updateConnection, isPending, error } = useUpdateConnection();

  const { control, handleSubmit, watch } = useForm({
    resolver: zodResolver(editSchema),
    defaultValues: {
      display_name: connection?.display_name || "",
      host_url: connection?.host_url || "",
      public_key: "",
      secret_key: "",
      sync_interval_seconds: connection?.sync_interval_seconds || 300,
    },
  });

  const publicKey = watch("public_key");
  const secretKey = watch("secret_key");
  const hasKeyChanges = Boolean(publicKey || secretKey);

  const onSubmit = (formData) => {
    const payload = {};

    // Only include fields that changed (skip empty strings to avoid wiping values)
    if (
      formData.display_name &&
      formData.display_name !== (connection?.display_name || "")
    ) {
      payload.display_name = formData.display_name;
    }
    if (formData.public_key) payload.public_key = formData.public_key;
    if (formData.secret_key) payload.secret_key = formData.secret_key;
    if (
      formData.host_url &&
      formData.host_url !== (connection?.host_url || "")
    ) {
      payload.host_url = formData.host_url;
    }
    if (
      formData.sync_interval_seconds !==
      (connection?.sync_interval_seconds || 300)
    ) {
      payload.sync_interval_seconds = formData.sync_interval_seconds;
    }

    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }

    updateConnection(
      { id: connection.id, data: payload },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <FormTextFieldV2
            control={control}
            fieldName="display_name"
            label="Display Name"
            fullWidth
            size="small"
          />
          <FormTextFieldV2
            control={control}
            fieldName="host_url"
            label="Host URL"
            fullWidth
            size="small"
          />
          <FormTextFieldV2
            control={control}
            fieldName="public_key"
            label="Public Key"
            fullWidth
            size="small"
            placeholder={
              connection?.public_key_display || "Leave blank to keep current"
            }
          />
          <FormTextFieldV2
            control={control}
            fieldName="secret_key"
            label="Secret Key"
            fullWidth
            size="small"
            type={showSecret ? "text" : "password"}
            placeholder="Leave blank to keep current"
            InputProps={{
              endAdornment: (
                <IconButton
                  onClick={() => setShowSecret(!showSecret)}
                  edge="end"
                >
                  <Iconify
                    icon={
                      showSecret ? "solar:eye-bold" : "solar:eye-closed-bold"
                    }
                  />
                </IconButton>
              ),
            }}
          />

          {hasKeyChanges && (
            <Alert severity="info">
              Changing API keys will re-validate the connection.
            </Alert>
          )}

          <Controller
            name="sync_interval_seconds"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                select
                label="Sync Interval"
                fullWidth
                size="small"
                SelectProps={{ native: true }}
                onChange={(e) => field.onChange(Number(e.target.value))}
              >
                {SYNC_INTERVALS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </TextField>
            )}
          />

          {error && (
            <Alert severity="error">
              {getErrorMessage(error, "Failed to update integration")}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          sx={outlinedNeutralButtonSx}
        >
          Cancel
        </Button>
        <LoadingButton
          type="submit"
          variant="contained"
          color="primary"
          size="small"
          loading={isPending}
        >
          Save Changes
        </LoadingButton>
      </DialogActions>
    </form>
  );
}

EditDialogContent.propTypes = {
  onClose: PropTypes.func.isRequired,
  connection: PropTypes.object.isRequired,
};

export default function EditIntegrationDialog({ open, onClose, connection }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Edit Integration</DialogTitle>
      {open && <EditDialogContent onClose={onClose} connection={connection} />}
    </Dialog>
  );
}

EditIntegrationDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  connection: PropTypes.object.isRequired,
};
