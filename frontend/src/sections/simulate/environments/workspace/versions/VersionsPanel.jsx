import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import {
  VERSION_PIECES,
  isVersioningDisabled,
  useEnvironmentVersions,
  useMoveDefault,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import HistoryEntry from "./HistoryEntry";
import { timelineEntries } from "./historyTimeline";

// No row open, as opposed to null: nothing picked yet, so the default shows.
const CLOSED = { closed: true };

// The History tab: every agent, environment and scenario version as one
// timeline, newest first. A row opens what changed in that version.
export default function VersionsPanel({ env }) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, isLoading, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  // A link (the unsaved-changes bar) may name the version to show first.
  const [params, setParams] = useSearchParams();
  const [linkedPiece, linkedId] = (params.get("version") || "").split(":");
  const [picked, setPicked] = useState(null);
  useEffect(() => {
    if (linkedId) setPicked({ piece: linkedPiece, id: linkedId });
  }, [linkedPiece, linkedId]);
  // Held here so a refusal outlives the refresh that re-renders the row.
  const move = useMoveDefault(env?.id);

  // Until the reader picks one, open the default environment version.
  const selection = useMemo(() => {
    if (!data || picked === CLOSED) return null;
    if (picked && data[picked.piece]?.some((v) => v.id === picked.id))
      return picked;
    const piece = data.defaults.environment
      ? "environment"
      : VERSION_PIECES.find((p) => data[p].length);
    if (!piece) return null;
    // The default first; otherwise the newest version that is not a draft.
    const version =
      data.defaults[piece] ||
      data[piece].find((v) => v.status !== "draft") ||
      data[piece][0];
    return { piece, id: version.id };
  }, [data, picked]);
  const entries = useMemo(() => timelineEntries(data), [data]);

  if (!enabled) return null;

  const frame = (children) => (
    <Stack spacing={2.5} sx={{ p: 3 }}>
      <Box>
        <Typography sx={{ typography: "m3", fontWeight: 500 }}>
          {VERSIONS_COPY.title}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.5 }}>
          {VERSIONS_COPY.subtitle}
        </Typography>
      </Box>
      {children}
    </Stack>
  );

  if (isLoading) return frame(<CircularProgress size={22} />);
  if (isVersioningDisabled(error)) {
    return frame(
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {VERSIONS_COPY.disabled}
      </Typography>,
    );
  }
  // A failed refresh keeps the versions it already has.
  if (!data) {
    return frame(
      <Typography sx={{ typography: "s2", color: "status.danger.fg" }}>
        {VERSIONS_COPY.loadError}
      </Typography>,
    );
  }
  if (data.empty) {
    return frame(
      <Box
        sx={{
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: "8px",
          p: 3,
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 500 }}>
          {VERSIONS_COPY.empty.title}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          {VERSIONS_COPY.empty.body}
        </Typography>
      </Box>,
    );
  }

  const openKey = selection ? `${selection.piece}:${selection.id}` : null;
  const pick = (entry, open) => {
    move.reset();
    setPicked(open ? { piece: entry.piece, id: entry.version.id } : CLOSED);
    // So the next link to a version counts as a change.
    if (params.has("version")) {
      const q = new URLSearchParams(params);
      q.delete("version");
      setParams(q, { replace: true });
    }
  };

  return frame(
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        "& > :first-of-type": { borderTop: 0 },
      }}
    >
      {entries.map((entry) => (
        <HistoryEntry
          key={entry.key}
          entry={entry}
          envId={env.id}
          move={move}
          siblings={data[entry.piece]}
          defaultId={data.defaults[entry.piece]?.id ?? null}
          expanded={entry.key === openKey}
          onToggle={() => pick(entry, entry.key !== openKey)}
          onOpen={() => entry.key !== openKey && pick(entry, true)}
        />
      ))}
    </Box>,
  );
}

VersionsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }).isRequired,
};
