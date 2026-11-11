import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, MenuItem, Stack, TextField, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import { ConfirmDialog } from "src/components/custom-dialog";
import { paths } from "src/routes/paths";
import { useMyEnvironments } from "src/api/simulate-environments/environments";
import { useCreateEvidenceImport } from "src/api/simulate-environments/evidenceImports";
import { EVIDENCE_COPY, IMPORT_PARAM, errorText } from "./evidence.constants";

// The environments a person may import into: the first page is enough to pick
// from, as the list's own first page is.
const ENVIRONMENTS_SHOWN = 100;

// From Observe: freeze the picked sessions or calls as an import for one
// environment, then open its review on that environment's Scenarios tab. A
// refusal keeps the dialog and the selection, and says why.
export default function CreateEnvironmentTestDialog({
  open,
  onClose,
  projectId,
  sourceKind,
  ids,
}) {
  const navigate = useNavigate();
  const { data, isError } = useMyEnvironments({
    page: 0,
    pageSize: ENVIRONMENTS_SHOWN,
  });
  const create = useCreateEvidenceImport();
  const [envId, setEnvId] = useState("");
  const environments = data?.rows ?? [];

  // "None yet" only once the list has answered; until then, or when it fails,
  // the dialog says so instead.
  let picker;
  if (environments.length) {
    picker = (
      <TextField
        select
        size="small"
        label={EVIDENCE_COPY.environmentLabel}
        value={envId}
        onChange={(event) => setEnvId(event.target.value)}
        inputProps={{ "data-testid": "evidence-environment" }}
      >
        {environments.map((one) => (
          <MenuItem key={one.id} value={one.id}>
            {one.name || one.id}
          </MenuItem>
        ))}
      </TextField>
    );
  } else if (isError) {
    picker = <Alert severity="error">{EVIDENCE_COPY.environmentsError}</Alert>;
  } else if (!data) {
    picker = (
      <Typography variant="body2" color="text.secondary">
        {EVIDENCE_COPY.environmentsLoading}
      </Typography>
    );
  } else {
    picker = <Alert severity="info">{EVIDENCE_COPY.noEnvironments}</Alert>;
  }

  const onCreate = () =>
    create.mutate(
      { envId, projectId, sourceKind, ids },
      {
        onSuccess: (made) => {
          onClose();
          navigate(
            `${paths.dashboard.simulate.environments.workspaceTab(envId, "scenarios")}&${IMPORT_PARAM}=${made.id}`,
          );
        },
      },
    );

  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      title={EVIDENCE_COPY.dialogTitle}
      content={
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Typography variant="body2">
            {EVIDENCE_COPY.dialogBody(ids.length, sourceKind)}
          </Typography>
          {picker}
          {create.error ? (
            <Alert severity="error">{errorText(create.error)}</Alert>
          ) : null}
        </Stack>
      }
      action={
        <LoadingButton
          variant="contained"
          disabled={!envId || !ids.length}
          loading={create.isPending}
          onClick={onCreate}
        >
          {EVIDENCE_COPY.create}
        </LoadingButton>
      }
    />
  );
}

CreateEnvironmentTestDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  projectId: PropTypes.string,
  sourceKind: PropTypes.oneOf(["session", "call"]).isRequired,
  ids: PropTypes.arrayOf(PropTypes.string).isRequired,
};
