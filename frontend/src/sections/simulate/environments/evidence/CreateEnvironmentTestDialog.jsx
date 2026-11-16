import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
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
  const { data, isError, refetch } = useMyEnvironments({
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
    picker = (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={() => refetch()}>
            {EVIDENCE_COPY.retry}
          </Button>
        }
      >
        {EVIDENCE_COPY.environmentsError}
      </Alert>
    );
  } else if (!data) {
    picker = (
      <ContentSkeleton
        variant="rows"
        rows={1}
        label={EVIDENCE_COPY.environmentsLoading}
      />
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
    // A short form: the design system's form dialog, md 560.
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={EVIDENCE_COPY.dialogTitle}
      actionBtnTitle={EVIDENCE_COPY.create}
      isValid={Boolean(envId && ids.length)}
      isLoading={create.isPending}
      onSubmit={onCreate}
    >
      <Stack spacing={2}>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {EVIDENCE_COPY.dialogBody(ids.length, sourceKind)}
        </Typography>
        {picker}
        {create.error ? (
          <Alert severity="error">{errorText(create.error)}</Alert>
        ) : null}
      </Stack>
    </ModalWrapper>
  );
}

CreateEnvironmentTestDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  projectId: PropTypes.string,
  sourceKind: PropTypes.oneOf(["session", "call"]).isRequired,
  ids: PropTypes.arrayOf(PropTypes.string).isRequired,
};
