import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import {
  VERSION_PIECES,
  isVersioningDisabled,
  useEnvironmentVersions,
  useMoveDefault,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import { named } from "../agents/agentVersionStatus";
import HistoryEntry from "./HistoryEntry";
import HistorySkeleton from "./HistorySkeleton";
import {
  HISTORY_KINDS,
  filterEntries,
  groupByDay,
  historyEntryId,
  timelineEntries,
} from "./historyTimeline";

const COPY = VERSIONS_COPY.history;
const MONO = "ui-monospace, Menlo, monospace";

const card = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
  minWidth: 0,
};

function Tile({ id, label, value, sub, mono = true }) {
  return (
    <Box
      data-testid={`history-tile-${id}`}
      sx={{ px: 2, py: 1.5, minWidth: 0, bgcolor: "background.paper" }}
    >
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", fontWeight: 500 }}
      >
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{
          typography: "m1",
          fontWeight: 600,
          fontFamily: mono ? MONO : undefined,
          mt: 0.5,
        }}
      >
        {value}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
      >
        {sub}
      </Typography>
    </Box>
  );
}
Tile.propTypes = {
  id: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  sub: PropTypes.node,
  // Version labels read in mono; counts don't.
  mono: PropTypes.bool,
};

// The History tab: every agent, environment and scenario version as one
// timeline, newest first. A row opens what changed in that version.
export default function VersionsPanel({ env }) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, isLoading, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  // A link may name the kind to show (the version bar) or the version to open
  // (the unsaved-changes bar). It holds until the reader picks something, which
  // drops it from the URL so that following it again counts.
  const [params, setParams] = useSearchParams();
  const linkedKind = VERSION_PIECES.includes(params.get("kind"))
    ? params.get("kind")
    : null;
  const [linkedPiece, linkedId] = (
    (!linkedKind && params.get("version")) ||
    ""
  ).split(":");
  const [own, setOwn] = useState({ kind: null, picked: null });
  const picked = linkedId ? { piece: linkedPiece, id: linkedId } : own.picked;
  // Environment first, unless it has no versions and another kind does.
  const startKind = data?.environment?.length
    ? "environment"
    : VERSION_PIECES.find((one) => data?.[one]?.length) ?? "environment";
  const kind =
    linkedKind ??
    (linkedId && VERSION_PIECES.includes(linkedPiece) ? linkedPiece : null) ??
    own.kind ??
    startKind;
  const choose = (next) => {
    setOwn({ kind, picked, ...next });
    if (params.has("version") || params.has("kind")) {
      const q = new URLSearchParams(params);
      q.delete("version");
      q.delete("kind");
      setParams(q, { replace: true });
    }
  };
  // Held here so a refusal outlives the refresh that re-renders the row.
  const move = useMoveDefault(env?.id);
  const entries = useMemo(() => timelineEntries(data), [data]);

  // A row opens only when the reader or a link picks it, and only while the
  // version is still listed.
  const openKey =
    // A hand-edited link may name something that isn't a piece.
    VERSION_PIECES.includes(picked?.piece) &&
    data?.[picked.piece]?.some((v) => v.id === picked.id)
      ? `${picked.piece}:${picked.id}`
      : null;
  const linkedListed =
    Boolean(linkedId) && openKey === `${linkedPiece}:${linkedId}`;
  useEffect(() => {
    if (!linkedListed) return;
    document
      .getElementById(historyEntryId(`${linkedPiece}:${linkedId}`))
      ?.scrollIntoView?.({ block: "nearest" });
  }, [linkedListed, linkedPiece, linkedId]);

  if (!enabled) return null;

  const frame = (children) => (
    <Stack spacing={2} sx={{ p: 3 }}>
      {children}
    </Stack>
  );

  if (isLoading) return <HistorySkeleton label={VERSIONS_COPY.loading} />;
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

  const pick = (entry, open) => {
    move.reset();
    choose({
      kind: entry.piece,
      picked: open ? { piece: entry.piece, id: entry.version.id } : null,
    });
  };

  const { tiles } = COPY;
  const envDefault = data.defaults.environment;
  const newerEnv = entries.some(
    (e) =>
      e.piece === "environment" &&
      e.badges.includes("latest") &&
      e.version.id !== envDefault?.id,
  );
  const groups = groupByDay(filterEntries(entries, kind));
  const suite = data.defaults.suite;
  const evaluations = env?.counts?.evaluations;

  return frame(
    <>
      <Box
        sx={{
          ...card,
          overflow: "hidden",
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: "repeat(4, minmax(0, 1fr))",
          },
          // The 1px gaps show the divider colour, so the lines stay right
          // however many tiles sit in a row.
          gap: "1px",
          bgcolor: "divider",
        }}
      >
        <Tile
          id="environment"
          label={tiles.environment}
          value={envDefault ? named(envDefault) : COPY.none}
          sub={envDefault && (newerEnv ? tiles.newer : tiles.inUse)}
        />
        <Tile
          id="agent"
          label={tiles.agent}
          value={data.defaults.agent ? named(data.defaults.agent) : COPY.none}
          sub={data.defaults.agent && tiles.agentSub}
        />
        <Tile
          id="scenarios"
          label={tiles.scenarios}
          value={suite ? named(suite) : COPY.none}
          sub={
            suite?.scenarioCount != null &&
            tiles.scenariosSub(suite.scenarioCount)
          }
        />
        <Tile
          id="evaluations"
          mono={false}
          label={tiles.evaluations}
          // A lone dash when the environment doesn't say how many.
          value={evaluations ?? "—"}
          sub={tiles.evaluationsSub}
        />
      </Box>
      <Box sx={{ ...card, overflow: "hidden" }}>
        {/* The card's width depends on the chat panel, not the viewport, so
            the filter wraps under the text once the text would get narrow. */}
        <Stack
          direction="row"
          flexWrap="wrap"
          useFlexGap
          gap={2}
          alignItems="flex-start"
          justifyContent="space-between"
          sx={{ px: 2.5, py: 2 }}
        >
          <Box sx={{ flex: "1 1 280px", minWidth: 0 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {COPY.title}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
            >
              {COPY.subtitle}
            </Typography>
          </Box>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={kind}
            // One kind is always picked; clicking it again keeps it.
            onChange={(_, next) => next && choose({ kind: next })}
            aria-label={COPY.filterLabel}
            sx={{ flexShrink: 0 }}
          >
            {HISTORY_KINDS.map((one) => (
              <ToggleButton key={one} value={one}>
                {COPY.filters[one]}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Stack>
        {groups.length === 0 && (
          <Typography
            sx={{
              typography: "s2",
              color: "text.subtitle",
              px: 2.5,
              py: 2,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {COPY.emptyKind[kind]}
          </Typography>
        )}
        {groups.map((group) => (
          <Box key={group.key}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 500,
                color: "text.subtitle",
                px: 2.5,
                py: 0.75,
                bgcolor: "action.hover",
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {group.label}
            </Typography>
            {group.entries.map((entry) => (
              <HistoryEntry
                key={entry.key}
                entry={entry}
                envId={env.id}
                move={move}
                siblings={data[entry.piece]}
                defaultId={data.defaults[entry.piece]?.id ?? null}
                expanded={entry.key === openKey}
                onToggle={() => pick(entry, entry.key !== openKey)}
              />
            ))}
          </Box>
        ))}
      </Box>
    </>,
  );
}

VersionsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
    counts: PropTypes.shape({ evaluations: PropTypes.number }),
  }).isRequired,
};
