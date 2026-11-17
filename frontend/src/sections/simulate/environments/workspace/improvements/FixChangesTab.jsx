import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import {
  fetchCandidatePatch,
  patchFileName,
} from "src/api/simulate-environments/fixRuns";

import { downloadText } from "./downloadText";
import { reviewTrialOf } from "./fixRunView";
import { parseDiff } from "./parseDiff";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";

const mono = {
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 12.5,
  lineHeight: "20px",
};

function DiffLine({ line }) {
  const add = line.type === "add";
  const remove = line.type === "remove";
  return (
    <Stack
      direction="row"
      sx={{
        bgcolor: add
          ? alpha(BUILD_TONES.green, 0.1)
          : remove
            ? alpha(BUILD_TONES.red, 0.1)
            : "transparent",
      }}
    >
      <Box
        sx={{
          ...mono,
          width: 24,
          flexShrink: 0,
          textAlign: "center",
          color: "text.disabled",
        }}
      >
        {add ? "+" : remove ? "−" : ""}
      </Box>
      <Typography
        component="span"
        sx={{
          ...mono,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          textDecoration: remove ? "line-through" : "none",
          color: remove ? "text.secondary" : "text.primary",
        }}
      >
        {line.text}
      </Typography>
    </Stack>
  );
}
DiffLine.propTypes = { line: PropTypes.object.isRequired };

// The design's Changes tab (WinnerChanges): the chosen trial's change against
// the version it started from, as a diff.
export default function FixChangesTab({ fix, candidateId }) {
  const trials = [...(fix.candidates || [])].sort(
    (a, b) => a.attempt - b.attempt,
  );
  // The trial "Review Trial N" or the trial picker asked for, else the winner,
  // else the trial that did best on its targets.
  const picked = candidateId || reviewTrialOf(fix)?.id;
  const candidate = trials.find((c) => c.id === picked);
  // A failure shows here, with a way to read it again, not as a global error.
  const {
    data: patch,
    isLoading,
    isError,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ["agent-fix-run-patch", fix.id, picked],
    queryFn: () => fetchCandidatePatch(fix.id, picked),
    enabled: !!picked,
    staleTime: Infinity,
    meta: { errorHandled: true },
  });
  if (!candidate) return null;
  const files = parseDiff(patch);
  const trial = `Trial ${candidate.attempt}`;
  const base = fix.agent?.version_number;
  // An empty patch is a trial that changed no file; a binary-only one still
  // has a patch to download, though it has no lines to show.
  const hasPatch = typeof patch === "string" && patch.trim() !== "";
  const empty = !isLoading && !isError && !hasPatch;
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1}
        sx={{ mb: 1.5 }}
      >
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {base != null
            ? `${trial}'s change against agent v${base}`
            : `${trial}'s change`}
        </Typography>
        {hasPatch && (
          <Button
            size="small"
            startIcon={<Iconify icon="solar:download-linear" width={16} />}
            onClick={() =>
              downloadText(patchFileName(fix.id, candidate.attempt), patch)
            }
          >
            Download patch
          </Button>
        )}
      </Stack>
      {isLoading && (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          Loading the change…
        </Typography>
      )}
      {isError && (
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {`Could not load ${trial}'s change.`}
          </Typography>
          <Button size="small" disabled={isFetching} onClick={() => refetch()}>
            Try again
          </Button>
        </Stack>
      )}
      {empty && (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {`${trial} made no file changes.`}
        </Typography>
      )}
      <Stack spacing={1.5}>
        {files.map((f) => (
          <Box
            key={f.file}
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "hidden",
            }}
          >
            <Typography
              sx={{
                ...mono,
                px: 1.5,
                py: 0.75,
                fontWeight: 600,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              {f.file}
            </Typography>
            {f.lines.map((line, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <DiffLine key={i} line={line} />
            ))}
          </Box>
        ))}
      </Stack>
    </Box>
  );
}

FixChangesTab.propTypes = {
  fix: PropTypes.object.isRequired,
  candidateId: PropTypes.string,
};
