// W7-4 remote scorers: the editor (create and edit). Your HTTPS service
// scores each row; Future AGI signs every call and checks every answer.
// Mounted by TypedEvalEditor on the create page ({name, onSave, saving}:
// onSave(route, body) posts to the create route, then opens the scorer, where
// the detail shows the signing secret once) and by RemoteScorerDetail (edit,
// `templateId` + `detail`).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import {
  REMOTE_CALLS_AT_A_TIME,
  REMOTE_OUTPUT_TYPES,
  REMOTE_RETRIES,
  REMOTE_TIMEOUT_MS,
  RETRY_CLASSES,
  SEND_MODES,
} from "src/sections/common/EvalTypes/remoteModel";
import { evalNameFormatError } from "../components/createNameError";
import RemoteAuthFields from "./RemoteAuthFields";
import RemoteInputsField from "./RemoteInputsField";
import RemoteTestPanel from "./RemoteTestPanel";
import SigningSecretDialog from "./SigningSecretDialog";
import {
  bodyOfForm,
  emptyRemoteForm,
  formErrors,
  formFromDetail,
  handOverSecret,
} from "./remoteScorerForm";

const range = (lo, hi) => Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);

export default function RemoteScorerEditor({
  name,
  onSave,
  saving: pageSaving = false,
  templateId,
  detail,
  onSaved,
}) {
  const [form, setForm] = useState(() =>
    detail ? formFromDetail(detail) : emptyRemoteForm(),
  );
  const [tried, setTried] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState(null);
  const [secret, setSecret] = useState(null);
  const [saved, setSaved] = useState(null);
  const set = (k) => (e) =>
    setForm((f) => ({ ...f, [k]: e?.target ? e.target.value : e }));
  const errors = formErrors(form, { detail });
  const shown = tried ? errors : {};
  const editing = Boolean(templateId);
  // FA-EVALS-02: on create, the same naming rule as Save Evaluation (said at
  // the Name field too). Editing keeps the stored name.
  const nameReason = editing
    ? null
    : !name?.trim()
      ? "Name the eval first."
      : evalNameFormatError(name);

  const save = async () => {
    setTried(true);
    setServerError(null);
    if (Object.keys(errors).length) return;
    setSaving(true);
    try {
      if (!editing && onSave) {
        // The create page posts (with the page's name and description) and
        // opens the new scorer; its detail shows the secret, once.
        const result = await onSave(
          endpoints.develop.w7.createRemote,
          bodyOfForm(form),
        );
        if (result?.id && result.signing_secret)
          handOverSecret(result.id, result.signing_secret);
        return;
      }
      const res = editing
        ? await axios.patch(
            endpoints.develop.w7.remote(templateId),
            bodyOfForm(form),
          )
        : await axios.post(
            endpoints.develop.w7.createRemote,
            bodyOfForm(form, { name }),
          );
      const result = res?.data?.result;
      setForm((f) => ({ ...f, token: "", clientSecret: "" }));
      if (result?.signing_secret) {
        setSecret(result.signing_secret);
        setSaved(result);
      } else onSaved?.(result);
    } catch (e) {
      setServerError(e?.result ?? e?.message ?? "Couldn't save the scorer");
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || pageSaving;
  return (
    <Stack spacing={2} data-testid="remote-scorer-editor">
      <Typography variant="body2" color="text.secondary">
        Your HTTPS service scores each row. Future AGI sends it the row and the
        output, signs every call, and checks every answer before it becomes a
        score.
      </Typography>
      <TextField
        size="small"
        label="Scorer URL"
        placeholder="https://scorer.example.com/score"
        value={form.url}
        onChange={set("url")}
        error={Boolean(shown.url)}
        helperText={
          shown.url ??
          "Runs call it over HTTPS. Private and local addresses are blocked."
        }
        inputProps={{ "data-testid": "remote-url" }}
      />
      <RemoteAuthFields form={form} set={set} shown={shown} detail={detail} />
      <Stack direction="row" spacing={1.5}>
        <TextField
          select
          size="small"
          label="Timeout"
          value={form.timeoutS}
          onChange={set("timeoutS")}
          sx={{ width: 120 }}
          inputProps={{ "data-testid": "remote-timeout" }}
        >
          {range(
            REMOTE_TIMEOUT_MS.min / 1000,
            REMOTE_TIMEOUT_MS.max / 1000,
          ).map((s) => (
            <MenuItem key={s} value={String(s)}>
              {s} s
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Retries"
          value={form.retries}
          onChange={set("retries")}
          sx={{ width: 110 }}
          inputProps={{ "data-testid": "remote-retries" }}
        >
          {range(REMOTE_RETRIES.min, REMOTE_RETRIES.max).map((n) => (
            <MenuItem key={n} value={String(n)}>
              {n}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Calls at a time"
          value={form.callsAtATime}
          onChange={set("callsAtATime")}
          sx={{ width: 140 }}
        >
          {range(REMOTE_CALLS_AT_A_TIME.min, REMOTE_CALLS_AT_A_TIME.max).map(
            (n) => (
              <MenuItem key={n} value={String(n)}>
                {n}
              </MenuItem>
            ),
          )}
        </TextField>
        <TextField
          select
          size="small"
          label="Send"
          value={form.send}
          onChange={set("send")}
          sx={{ minWidth: 280 }}
        >
          {SEND_MODES.map((s) => (
            <MenuItem key={s.value} value={s.value}>
              {s.label}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      {Number(form.retries) > 0 && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="caption" color="text.secondary">
            Retry on
          </Typography>
          {RETRY_CLASSES.map((c) => (
            <FormControlLabel
              key={c.value}
              control={
                <Checkbox
                  size="small"
                  checked={form.retryOn.includes(c.value)}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      retryOn: e.target.checked
                        ? [...f.retryOn, c.value]
                        : f.retryOn.filter((x) => x !== c.value),
                    }))
                  }
                />
              }
              label={<Typography variant="caption">{c.label}</Typography>}
            />
          ))}
          <Typography variant="caption" color="text.secondary">
            Waits Retry-After, else 1 s, 2 s, 4 s. 413 is never retried.
          </Typography>
        </Stack>
      )}
      {shown.retry_on && <Alert severity="warning">{shown.retry_on}</Alert>}
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <TextField
          select
          size="small"
          label="It answers"
          value={form.outputType}
          onChange={set("outputType")}
          sx={{ minWidth: 180 }}
          inputProps={{ "data-testid": "remote-output" }}
        >
          {REMOTE_OUTPUT_TYPES.map((o) => (
            <MenuItem key={o.value} value={o.value}>
              {o.label}
            </MenuItem>
          ))}
        </TextField>
        {form.outputType !== "pass_fail" && (
          <TextField
            size="small"
            label="Pass at"
            placeholder="0.5"
            value={form.passThreshold}
            onChange={set("passThreshold")}
            sx={{ width: 100 }}
          />
        )}
        {form.outputType === "choice" && (
          <TextField
            multiline
            minRows={2}
            size="small"
            label="Choices (one a line: label = score)"
            placeholder={"good = 1\nbad = 0"}
            value={form.choices}
            onChange={set("choices")}
            error={Boolean(shown.choices)}
            helperText={shown.choices}
            sx={{ flex: 1 }}
          />
        )}
      </Stack>
      <RemoteInputsField
        inputs={form.inputs}
        onChange={(inputs) => setForm((f) => ({ ...f, inputs }))}
        error={shown.mapping}
      />
      {(serverError || (tried && Object.keys(errors).length > 0)) && (
        <Alert severity="error" data-testid="remote-save-error">
          {serverError ?? Object.values(errors)[0]}
        </Alert>
      )}
      <Stack direction="row" spacing={1}>
        <Button
          variant="contained"
          size="small"
          onClick={save}
          disabled={busy || Boolean(nameReason)}
          data-testid="remote-save"
        >
          {busy ? "Saving…" : editing ? "Save changes" : "Create remote scorer"}
        </Button>
        {nameReason && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ alignSelf: "center" }}
          >
            {nameReason}
          </Typography>
        )}
      </Stack>
      <RemoteTestPanel
        templateId={templateId}
        form={form}
        name={name ?? detail?.name}
      />
      <SigningSecretDialog
        open={Boolean(secret)}
        secret={secret ?? ""}
        onClose={() => {
          setSecret(null);
          onSaved?.(saved);
        }}
      />
    </Stack>
  );
}

RemoteScorerEditor.ready = true;

RemoteScorerEditor.propTypes = {
  name: PropTypes.string,
  onSave: PropTypes.func,
  saving: PropTypes.bool,
  templateId: PropTypes.string,
  detail: PropTypes.object,
  onSaved: PropTypes.func,
};
