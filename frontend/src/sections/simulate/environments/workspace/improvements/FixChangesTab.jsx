import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { useState } from "react";

import Iconify from "src/components/iconify";
import {
  fetchCandidatePatch,
  patchFileName,
} from "src/api/simulate-environments/fixRuns";

import { downloadText } from "./downloadText";
import { OUTCOME_LABEL, winnerOf } from "./fixRunView";
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

const label = (c) =>
  `Trial ${c.attempt} · ${OUTCOME_LABEL[c.outcome] || c.outcome}`;

// The design's Changes tab (WinnerChanges): the chosen trial's change against
// the version it started from, as a diff.
export default function FixChangesTab({ fix, candidateId }) {
  const trials = [...(fix.candidates || [])].sort(
    (a, b) => a.attempt - b.attempt,
  );
  const [picked, setPicked] = useState(
    candidateId || winnerOf(fix)?.id || trials[0]?.id,
  );
  const candidate = trials.find((c) => c.id === picked);
  const { data: patch, isLoading } = useQuery({
    queryKey: ["agent-fix-run-patch", fix.id, picked],
    queryFn: () => fetchCandidatePatch(fix.id, picked),
    enabled: !!picked,
    staleTime: Infinity,
  });
  if (!candidate) return null;
  const files = parseDiff(patch);
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <Select
          size="small"
          value={picked}
          onChange={(e) => setPicked(e.target.value)}
          renderValue={() => label(candidate)}
        >
          {trials.map((c) => (
            <MenuItem key={c.id} value={c.id}>
              {label(c)}
            </MenuItem>
          ))}
        </Select>
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
          noWrap
        >
          {candidate.summary}
        </Typography>
        {patch && (
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
