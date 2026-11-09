import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { useCreateWebhook, usePreview } from "src/api/webhooks";
import { NO_REPLAY } from "./copy";
import { ALL_EVENTS, apiError, createBody, emptyForm, headersOf } from "./form";
import WebhookFields from "./WebhookFields";
import { prettyJson } from "./text";

// "What your endpoint receives": the exact request for a sample of the first
// checked event, rendered by POST /webhooks/preview/ (signature masked).
export function RequestPreview({ form }) {
  const preview = usePreview();
  const first = ALL_EVENTS.find((t) => form.events[t]);
  const headers = headersOf(form);
  const key = JSON.stringify([
    first,
    form.payloadMode,
    form.includePeopleNames,
    headers,
  ]);
  const { mutate } = preview;

  useEffect(() => {
    if (!first) return undefined;
    const t = setTimeout(
      () =>
        mutate({
          event_type: first,
          payload_mode: form.payloadMode,
          include_people_names: form.includePeopleNames,
          headers,
        }),
      300,
    );
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, mutate]);

  const data = preview.data?.data?.result;
  return (
    <Box
      aria-label="What your endpoint receives"
      className={NO_REPLAY}
      sx={{
        p: 2,
        bgcolor: "background.neutral",
        height: "100%",
        overflow: "auto",
      }}
    >
      <Typography variant="subtitle2" gutterBottom>
        What your endpoint receives
      </Typography>
      {!first && (
        <Typography variant="body2" color="text.secondary">
          Check an event to see the request.
        </Typography>
      )}
      {first && preview.isError && (
        <Alert severity="error">{apiError(preview.error).message}</Alert>
      )}
      {first && data && (
        <Box
          component="pre"
          sx={{
            m: 0,
            fontFamily: "monospace",
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {`${data.method} <your endpoint URL>\n`}
          {data.headers.map(([k, v]) => `${k}: ${v}`).join("\n")}
          {"\n\n"}
          {prettyJson(data.body)}
        </Box>
      )}
    </Box>
  );
}

RequestPreview.propTypes = { form: PropTypes.object.isRequired };

export default function AddWebhookDialog({
  open,
  onClose,
  onCreated,
  access,
  orgPage,
  fullPayloadAllowed,
  urlPolicy,
}) {
  const initial = useMemo(
    () => emptyForm({ orgWide: !!(orgPage && access?.org_admin) }),
    [orgPage, access?.org_admin],
  );
  const [form, setForm] = useState(initial);
  const [error, setError] = useState(null);
  const create = useCreateWebhook();

  useEffect(() => {
    if (open) {
      setForm(initial);
      setError(null);
    }
  }, [open, initial]);

  const canSave =
    form.name.trim() &&
    form.url.trim() &&
    ALL_EVENTS.some((t) => form.events[t]);

  // The new endpoint is made in the workspace the section shows
  // (access.workspace_id: the page's workspace, see WebhooksScope).
  const save = () => {
    setError(null);
    create.mutate(createBody(form, { workspaceId: access?.workspace_id }), {
      onSuccess: (res) => {
        onCreated(res.data.result);
        // The secret now lives only in the secret dialog's props; the
        // mutation's cached answer is dropped (review S8).
        create.reset();
      },
      onError: (err) => setError(apiError(err, "Could not save the webhook.")),
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { width: 980, maxWidth: "96vw" } }}
    >
      <DialogTitle>Add webhook</DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        <Stack direction={{ xs: "column", md: "row" }} sx={{ minHeight: 480 }}>
          <Box sx={{ width: { md: 560 }, flexShrink: 0, p: 2.5 }}>
            {error && !["name", "url"].includes(error.attr) && (
              <Alert severity="error" sx={{ mb: 2 }} role="alert">
                {error.message}
              </Alert>
            )}
            <WebhookFields
              form={form}
              setForm={setForm}
              access={access}
              fullPayloadAllowed={fullPayloadAllowed}
              urlPolicy={urlPolicy}
              fieldError={error}
              showScope={!!access?.org_admin}
            />
          </Box>
          <Box
            sx={{
              flex: 1,
              minWidth: 0,
              borderLeft: { md: 1 },
              borderColor: { md: "divider" },
            }}
          >
            <RequestPreview form={form} />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={save}
          disabled={!canSave || create.isPending}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

AddWebhookDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onCreated: PropTypes.func,
  access: PropTypes.object,
  orgPage: PropTypes.bool,
  fullPayloadAllowed: PropTypes.bool,
  urlPolicy: PropTypes.object,
};
