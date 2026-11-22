import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Box, Button, Stack, Typography } from "@mui/material";

import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import { fetchCandidatePatch } from "src/api/simulate-environments/fixRuns";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { provenOnTargetsOnly, strengthLabel } from "./fixRunView";
import { parseDiff } from "./parseDiff";

const num = { fontVariantNumeric: "tabular-nums" };

// A destination fact: label, then the value in a quiet code chip with its icon.
function Row({ label, icon, value }) {
  return (
    <Stack direction="row" spacing={1.5} alignItems="center">
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 56,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        sx={{
          minWidth: 0,
          px: 1,
          py: 0.25,
          borderRadius: "6px",
          bgcolor: "action.hover",
        }}
      >
        <Iconify
          icon={icon}
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: "text.primary",
            fontFamily: "monospace",
          }}
        >
          {value}
        </Typography>
      </Stack>
    </Stack>
  );
}
Row.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
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
  const files = parseDiff(data);
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        maxHeight: 336,
        overflowY: "auto",
      }}
    >
      {files.map((f, i) => {
        const added = f.lines.filter((l) => l.type === "add").length;
        const removed = f.lines.filter((l) => l.type === "remove").length;
        return (
          <Stack
            key={f.file}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 1.5,
              minHeight: 32,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:document-text-linear"
              width={14}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            <Typography
              noWrap
              title={f.file}
              sx={{
                typography: "s2",
                color: "text.primary",
                fontFamily: "monospace",
                flex: 1,
                minWidth: 0,
              }}
            >
              {f.file}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 500,
                color: "status.success.fg",
                width: 28,
                textAlign: "right",
                flexShrink: 0,
                ...num,
              }}
            >
              +{added}
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 500,
                color: removed ? "status.danger.fg" : "text.disabled",
                width: 28,
                textAlign: "right",
                flexShrink: 0,
                ...num,
              }}
            >
              −{removed}
            </Typography>
          </Stack>
        );
      })}
    </Box>
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
  // Too few held back to verify it, none included: the result says how strong it is.
  const strength = strengthLabel(fix);
  const held = provenOnTargetsOnly(fix) ? "nothing was" : "too few cases were";
  const strengthNote = github
    ? `${strength}: it opens as a draft pull request, marked ${strength} in its title and body.`
    : `${strength}: ${held} held back to prove this change.`;
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
      PaperProps={{ sx: { maxWidth: 480 } }}
      content={
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <Stack spacing={1}>
            {github ? (
              <>
                {fix.agent?.repository && (
                  <Row
                    label="Repo"
                    icon="mdi:github"
                    value={fix.agent.repository}
                  />
                )}
                {fix.agent?.ref && (
                  <Row
                    label="Into"
                    icon="solar:git-branch-linear"
                    value={fix.agent.ref}
                  />
                )}
              </>
            ) : (
              <Row
                label="Version"
                icon="solar:layers-minimalistic-linear"
                value={`${fix.agent?.name ?? "Agent"} · v${winner.version_number}`}
              />
            )}
          </Stack>
          <Box>
            <Typography
              sx={{ typography: "s2", color: "text.subtitle", mb: 0.75 }}
            >
              Change
            </Typography>
            {open && <ChangeSummary fix={fix} winner={winner} />}
          </Box>
          {strength && (
            <Stack direction="row" spacing={0.75} alignItems="flex-start">
              <Iconify
                icon="solar:danger-triangle-linear"
                width={15}
                sx={{ color: BUILD_TONES.amber, flexShrink: 0, mt: "2px" }}
              />
              <Typography sx={{ typography: "s2", color: BUILD_TONES.amber }}>
                {strengthNote}
              </Typography>
            </Stack>
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
