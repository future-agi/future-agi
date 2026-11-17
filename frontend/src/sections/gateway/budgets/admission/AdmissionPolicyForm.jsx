import PropTypes from "prop-types";
import { Icon } from "@iconify/react";
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AdmissionBindingsEditor from "./AdmissionBindingsEditor";
import {
  AMOUNT_CONTRACT,
  effectiveMicros,
  formatMicros,
  parseUsd,
} from "./admissionPolicy";

// What the gateway will actually enforce for the amount typed, or why it is
// refused. The same parse the save uses, so the caption cannot disagree.
function AmountCaption({ text, testId }) {
  let content;
  let error = false;
  if (String(text).trim() === "") {
    content = "Enter a limit; 0 is a hard zero-dollar cap, not unlimited.";
  } else {
    try {
      const micros = effectiveMicros(parseUsd(text).value);
      content = `Enforced as ${micros.toLocaleString("en-US")} micro-dollars (${formatMicros(micros)}).`;
    } catch (failure) {
      content = failure.message;
      error = true;
    }
  }
  return (
    <Typography
      variant="caption"
      color={error ? "error" : "text.secondary"}
      data-testid={testId}
    >
      {content}
    </Typography>
  );
}
AmountCaption.propTypes = {
  text: PropTypes.string.isRequired,
  testId: PropTypes.string.isRequired,
};

export default function AdmissionPolicyForm({
  draft,
  onChange,
  busy,
  revision,
  validation,
  canActivate,
  activationNotice,
  report,
  keys,
  onReloadKeys,
  onActivate,
  onCancel,
}) {
  const updateRow = (kind, index, field, value) =>
    onChange({
      ...draft,
      [kind]: draft[kind].map((row, i) =>
        i === index ? { ...row, [field]: value } : row,
      ),
    });
  return (
    <Stack spacing={2} data-testid="admission-draft">
      <Typography variant="subtitle2">
        Local draft · based on revision {revision}
      </Typography>
      <Alert severity="info">
        Activate writes and activates these caps immediately, independently of
        Save &amp; Activate below. It enables legacy budgets as a prerequisite,
        uses fail-closed checks, clamps missing max_tokens, and rejects unpriced
        requests. Review existing hierarchical budgets before activating.
      </Alert>
      <Box
        component="fieldset"
        disabled={busy}
        sx={{ border: 0, p: 0, m: 0, minWidth: 0 }}
      >
        <Stack spacing={2}>
          <Typography
            variant="body2"
            color="text.secondary"
            data-testid="admission-amount-contract"
          >
            {AMOUNT_CONTRACT}
          </Typography>
          {[
            ["agents", "Agent", "agent"],
            ["key_ids", "Key", "key"],
          ].map(([kind, label, action]) => (
            <Stack spacing={1} key={kind}>
              <Typography variant="subtitle2">
                {label} caps · Daily (UTC) · Hard block
              </Typography>
              {draft[kind].map((row, index) => (
                <Stack key={index} spacing={0.5}>
                  <Stack
                    direction={{ xs: "column", sm: "row" }}
                    spacing={1}
                    alignItems={{ sm: "center" }}
                  >
                    <TextField
                      size="small"
                      fullWidth
                      label={`${label} ${index + 1} identity`}
                      value={row.id}
                      onChange={(event) =>
                        updateRow(kind, index, "id", event.target.value)
                      }
                    />
                    <TextField
                      size="small"
                      fullWidth
                      label={`${label} ${index + 1} limit (USD)`}
                      type="number"
                      value={row.limit}
                      inputProps={{ min: 0, max: 1_000_000_000, step: "any" }}
                      onChange={(event) =>
                        updateRow(kind, index, "limit", event.target.value)
                      }
                    />
                    <IconButton
                      size="small"
                      aria-label={`Remove ${label.toLowerCase()} ${index + 1} from draft`}
                      onClick={() =>
                        onChange({
                          ...draft,
                          [kind]: draft[kind].filter((_, i) => i !== index),
                        })
                      }
                    >
                      <Icon icon="solar:trash-bin-trash-linear" width={16} />
                    </IconButton>
                  </Stack>
                  <AmountCaption
                    text={row.limit}
                    testId={`admission-effective-${kind}-${index}`}
                  />
                </Stack>
              ))}
              <Button
                size="small"
                variant="outlined"
                sx={{ alignSelf: "flex-start" }}
                onClick={() =>
                  onChange({
                    ...draft,
                    [kind]: [...draft[kind], { id: "", limit: "" }],
                  })
                }
              >
                Add {action} cap
              </Button>
            </Stack>
          ))}
          <AdmissionBindingsEditor
            draft={draft}
            onChange={onChange}
            busy={busy}
            report={report}
            keys={keys}
            onReloadKeys={onReloadKeys}
          />
          <Stack spacing={0.5}>
            <TextField
              size="small"
              label="Per-run ceiling (USD)"
              type="number"
              value={draft.runLimit}
              inputProps={{ min: 0, max: 1_000_000_000, step: "any" }}
              onChange={(event) =>
                onChange({ ...draft, runLimit: event.target.value })
              }
              helperText="Blank removes the configured run ceiling for new runs; 0 is a hard zero-dollar ceiling. This is not a daily run cap."
            />
            {draft.runLimit !== "" && (
              <AmountCaption
                text={draft.runLimit}
                testId="admission-effective-run"
              />
            )}
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Agent identities come from the bindings above. This editor writes
            them in the same revision-checked save, preserves every other
            routing and rollout setting, and never edits a rollout: a
            conflicting rollout is refused by the server, not silently changed.
            Drafts are local and disappear when closed or when the organization
            or role changes.
          </Typography>
        </Stack>
      </Box>
      {validation && (
        <Alert severity="warning" data-testid="admission-validation">
          {validation}
        </Alert>
      )}
      {activationNotice}
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button
          data-testid="admission-activate"
          variant="contained"
          onClick={onActivate}
          disabled={busy || !canActivate}
        >
          Activate draft
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Discard admission draft
        </Button>
      </Stack>
    </Stack>
  );
}
AdmissionPolicyForm.propTypes = {
  draft: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  revision: PropTypes.number.isRequired,
  validation: PropTypes.string,
  canActivate: PropTypes.bool,
  // Why Activate is disabled, shown beside it (e.g. no spend ledger).
  activationNotice: PropTypes.node,
  report: PropTypes.object.isRequired,
  keys: PropTypes.object.isRequired,
  onReloadKeys: PropTypes.func.isRequired,
  onActivate: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};
