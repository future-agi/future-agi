import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Box, Button, Stack, Typography } from "@mui/material";

import { ConfirmDialog } from "src/components/custom-dialog";
import { fetchCandidatePatch } from "src/api/simulate-environments/fixRuns";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { parseDiff } from "./parseDiff";

const num = { fontVariantNumeric: "tabular-nums" };

function Row({ label, children }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="flex-start">
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 64,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function ChangeSummary({ fix, winner }) {
  // Same key and staleness as the Changes tab, so a patch is read once. A
  // failure shows inline below, not as a second, global error.
  const { data, isLoading, error } = useQuery({
    queryKey: ["agent-fix-run-patch", fix.id, winner.id],
    queryFn: () => fetchCandidatePatch(fix.id, winner.id),
    staleTime: Infinity,
    meta: { errorHandled: true },
  });
  if (isLoading) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Reading the change…
      </Typography>
    );
  }
  if (error) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Could not read the change.
      </Typography>
    );
  }
  return (
    <Stack spacing={0.25}>
      {parseDiff(data).map((f) => {
        const added = f.lines.filter((l) => l.type === "add").length;
        const removed = f.lines.filter((l) => l.type === "remove").length;
        return (
          <Stack key={f.file} direction="row" spacing={1} alignItems="baseline">
            <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
              {f.file}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.secondary", ...num }}
            >
              {`+${added} −${removed}`}
            </Typography>
          </Stack>
        );
      })}
    </Stack>
  );
}
ChangeSummary.propTypes = {
  fix: PropTypes.object.isRequired,
  winner: PropTypes.object.isRequired,
};

// What Ship is about to do, before it does it: where a GitHub agent's pull
// request goes, or which version becomes the default, and the change itself.
export default function ShipConfirmDialog({
  fix,
  winner,
  open,
  busy,
  onClose,
  onConfirm,
}) {
  const github = fix.agent?.kind === "github";
  const limitedNote = github
    ? "Limited validation: it opens as a draft pull request, marked Limited validation in its title and body."
    : "Limited validation: too few cases were held back to prove this change.";
  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      closeDisabled={busy}
      title={
        github
          ? `Open a pull request for Candidate ${winner.attempt}?`
          : `Make Candidate ${winner.attempt}'s version the default?`
      }
      content={
        <Stack spacing={1.25}>
          {github ? (
            <>
              {fix.agent?.repository && (
                <Row label="Repo">
                  <Typography sx={{ typography: "s2" }}>
                    {fix.agent.repository}
                  </Typography>
                </Row>
              )}
              {fix.agent?.ref && (
                <Row label="Into">
                  <Typography sx={{ typography: "s2" }}>
                    {fix.agent.ref}
                  </Typography>
                </Row>
              )}
            </>
          ) : (
            <Row label="Version">
              <Typography sx={{ typography: "s2" }}>
                {`${fix.agent?.name ?? "Agent"} · v${winner.version_number}`}
              </Typography>
            </Row>
          )}
          <Row label="Change">
            {open && <ChangeSummary fix={fix} winner={winner} />}
          </Row>
          {fix.limited_validation && (
            <Typography sx={{ typography: "s2", color: BUILD_TONES.amber }}>
              {limitedNote}
            </Typography>
          )}
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={busy}
          onClick={onConfirm}
        >
          {github ? "Open pull request" : "Make default"}
        </Button>
      }
    />
  );
}

ShipConfirmDialog.propTypes = {
  fix: PropTypes.object.isRequired,
  winner: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  busy: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};
