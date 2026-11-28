import PropTypes from "prop-types";
import { Alert, Button, Stack, Typography } from "@mui/material";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";

const C = ACTORS_COPY.worldActors;
const F = C.form;

export default function ActorFormFooter({
  problems,
  overBy,
  check,
  checking,
  canSave,
  busy,
  onCancel,
  onSave,
}) {
  return (
    <>
      {(problems.length > 0 || overBy > 0) && (
        <Alert
          severity="error"
          sx={{ mx: 2.5, mb: 1.5, flexShrink: 0 }}
          data-testid="actor-problems"
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {F.problemsTitle}
          </Typography>
          {problems.map((one, index) => (
            <Typography key={index} sx={{ typography: "s2" }}>
              {one.message}
            </Typography>
          ))}
          {overBy > 0 && (
            <Typography sx={{ typography: "s2" }}>
              {F.tooLong(overBy)}
            </Typography>
          )}
        </Alert>
      )}
      {check.isError && (
        <Alert
          severity="error"
          sx={{ mx: 2.5, mb: 1.5, flexShrink: 0 }}
          action={
            <Button size="small" color="inherit" onClick={check.retry}>
              {F.retryCheck}
            </Button>
          }
        >
          {refusalText(check.error, F.checkFailed)}
        </Alert>
      )}
      <Stack
        direction="row"
        spacing={1}
        justifyContent="flex-end"
        sx={{
          px: 2.5,
          py: 2,
          flexShrink: 0,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {checking && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              alignSelf: "center",
            }}
          >
            {F.checking}
          </Typography>
        )}
        <Button onClick={onCancel} disabled={busy}>
          {F.cancel}
        </Button>
        <Button
          variant="contained"
          color="primary"
          disabled={!canSave}
          onClick={onSave}
        >
          {F.save}
        </Button>
      </Stack>
    </>
  );
}

ActorFormFooter.propTypes = {
  // Problems no field on screen shows.
  problems: PropTypes.arrayOf(
    PropTypes.shape({ field: PropTypes.string, message: PropTypes.string }),
  ).isRequired,
  // Characters over the server's bound on one record.
  overBy: PropTypes.number.isRequired,
  check: PropTypes.shape({
    isError: PropTypes.bool,
    error: PropTypes.object,
    retry: PropTypes.func,
  }).isRequired,
  checking: PropTypes.bool,
  canSave: PropTypes.bool,
  busy: PropTypes.bool,
  onCancel: PropTypes.func,
  onSave: PropTypes.func,
};
