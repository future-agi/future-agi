import PropTypes from "prop-types";
import { Box, Button, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useEnvironmentSources } from "src/api/simulate-environments/sources";
import { CONSOLE_COPY } from "../../buildEnvironment/build.constants";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { CHAT_FILES_COPY as COPY } from "./chatFiles";
import { PROPOSALS_COPY } from "./proposals";
import { ROLES } from "./sources.constants";

const TONE = {
  unreadable: "status.danger.fg",
  unsupported: "status.danger.fg",
  failed: "status.danger.fg",
};

// A row's state, from the upload and then the live summary.
const rowOf = (file, source, prepared, failed) => {
  if (failed) return { status: "failed", note: COPY.failedNote };
  if (!prepared || !source) {
    return {
      status: prepared ? "ready" : "uploading",
      note: CONSOLE_COPY.fileSize(file.size),
    };
  }
  if (source.status === "reading") return { status: "reading" };
  if (["unreadable", "unsupported"].includes(source.status))
    return { status: source.status, note: source.status_note };
  const parsed = source.ui?.parsed;
  return {
    status: "ready",
    note: [
      ROLES.find((role) => role.id === source.role)?.label,
      parsed?.kind === "table" && COPY.rows(parsed),
      parsed?.kind === "text" && COPY.passages(parsed.passages.length),
    ]
      .filter(Boolean)
      .join(" · "),
  };
};

/**
 * Files attached in builder chat, one row each: uploading, reading, then
 * Ready with what it's used as, or why it can't be used. Read from the
 * environment's sources, where they show as Not saved yet.
 */
export default function AttachmentsCard({ envId, files, prepared, failed }) {
  const { data: summary } = useEnvironmentSources(envId);
  const sources = summary?.sources || [];

  return (
    <Stack spacing={1}>
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Typography sx={{ typography: "m3", flex: 1 }}>
          {COPY.reading(files.length)}
        </Typography>
        <UiOnlyFlag feature="environmentSources" />
      </Stack>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {files.map((file) => {
          const source = sources.find(
            (one) => one.name === file.name && one.size === file.size,
          );
          const row = rowOf(file, source, prepared, failed);
          const busy = row.status === "uploading" || row.status === "reading";
          return (
            <Box
              key={`${file.name}-${file.size}`}
              sx={{
                px: 1.25,
                py: 1,
                "& + &": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify
                  icon="solar:file-text-linear"
                  width={16}
                  sx={{ color: "text.secondary", flexShrink: 0 }}
                />
                <Typography
                  noWrap
                  sx={{
                    typography: "s1",
                    fontWeight: 500,
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {file.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: TONE[row.status] || "text.secondary",
                    flexShrink: 0,
                  }}
                >
                  {COPY.status[row.status]}
                </Typography>
              </Stack>
              {busy && <LinearProgress sx={{ mt: 0.75, height: 2 }} />}
              {row.note && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
                >
                  {row.note}
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
    </Stack>
  );
}

AttachmentsCard.propTypes = {
  envId: PropTypes.string.isRequired,
  files: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string, size: PropTypes.number }),
  ).isRequired,
  // The prepare response, once the upload is read.
  prepared: PropTypes.object,
  failed: PropTypes.bool,
};

/**
 * What became of the proposals in the chat: added to Review changes, with
 * the blocked items in Couldn't apply, or dismissed.
 */
export function ProposalsOutcome({ outcome, nextLabel, onReviewChanges }) {
  if (!outcome)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {COPY.rejected}
      </Typography>
    );
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Box sx={{ px: 1.5, py: 1.25 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {PROPOSALS_COPY.cardTitle}
        </Typography>
        <Typography sx={{ typography: "s1" }}>
          {COPY.added(outcome.accepted.length)}
        </Typography>
        {outcome.failed.length > 0 && (
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {COPY.couldntApply(outcome.failed.length)}
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {nextLabel ? COPY.savedAs(nextLabel) : PROPOSALS_COPY.footer.saved}
        </Typography>
        {onReviewChanges && (
          <Button variant="outlined" size="small" onClick={onReviewChanges}>
            {PROPOSALS_COPY.reviewChanges}
          </Button>
        )}
      </Stack>
    </Box>
  );
}

ProposalsOutcome.propTypes = {
  // `{accepted, failed}` once added; null when dismissed.
  outcome: PropTypes.shape({
    accepted: PropTypes.array.isRequired,
    failed: PropTypes.array.isRequired,
  }),
  // The environment version Save makes, like "v4".
  nextLabel: PropTypes.string,
  onReviewChanges: PropTypes.func,
};
