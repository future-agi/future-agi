import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  CircularProgress,
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

function Tile({ id, label, value, sub }) {
  return (
    <Box
      data-testid={`history-tile-${id}`}
      sx={{ px: 2, py: 1.5, minWidth: 0 }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Typography
        noWrap
        sx={{ typography: "m3", fontWeight: 600, fontFamily: MONO, mt: 0.5 }}
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
};

function NowRow({ label, value }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 120,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography sx={{ typography: "s2", minWidth: 0 }} noWrap>
        {value}
      </Typography>
    </Stack>
  );
}
NowRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

// What new runs use: each piece's default, as the versions list states it.
function RunsUseNow({ data, env }) {
  const { now } = COPY;
  const label = (version) => (version ? named(version) : COPY.none);
  const suite = data.defaults.suite;
  const evaluations = env?.counts?.evaluations;
  return (
    <Box data-testid="history-now" sx={{ ...card, alignSelf: "start" }}>
      <Typography sx={{ typography: "s1", fontWeight: 600, px: 2, py: 1.5 }}>
        {now.title}
      </Typography>
      <NowRow
        label={now.environment}
        value={label(data.defaults.environment)}
      />
      <NowRow
        label={now.scenarios}
        value={
          suite
            ? now.scenariosValue(named(suite), suite.scenarioCount)
            : COPY.none
        }
      />
      {evaluations != null && (
        <NowRow
          label={now.evaluations}
          value={now.evaluationsValue(evaluations)}
        />
      )}
      <NowRow label={now.agent} value={label(data.defaults.agent)} />
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          px: 2,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {now.footer}
      </Typography>
    </Box>
  );
}
RunsUseNow.propTypes = {
  data: PropTypes.object.isRequired,
  env: PropTypes.object,
};

// The History tab: every agent, environment and scenario version as one
// timeline, newest first. A row opens what changed in that version.
export default function VersionsPanel({ env }) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, isLoading, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  // A link (the unsaved-changes bar) may name the version to open.
  const [params, setParams] = useSearchParams();
  const [linkedPiece, linkedId] = (params.get("version") || "").split(":");
  const [picked, setPicked] = useState(null);
  const [kind, setKind] = useState("all");
  useEffect(() => {
    if (!linkedId) return;
    setPicked({ piece: linkedPiece, id: linkedId });
    // A filter could hide the linked row; show every kind again.
    setKind("all");
  }, [linkedPiece, linkedId]);
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

  const pick = (entry, open) => {
    move.reset();
    setPicked(open ? { piece: entry.piece, id: entry.version.id } : null);
    // So the next link to a version counts as a change.
    if (params.has("version")) {
      const q = new URLSearchParams(params);
      q.delete("version");
      setParams(q, { replace: true });
    }
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

  return frame(
    <>
      <Box
        sx={{
          ...card,
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0, 1fr))" },
          "& > :not(:first-of-type)": {
            borderLeft: { sm: "1px solid" },
            borderTop: { xs: "1px solid", sm: 0 },
            borderColor: "divider",
          },
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
          id="versions"
          label={tiles.versions}
          value={entries.length}
          sub={tiles.counts(
            data.environment.length,
            data.suite.length,
            data.agent.length,
          )}
        />
      </Box>
      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: {
            xs: "1fr",
            md: "minmax(0, 2fr) minmax(0, 1fr)",
          },
        }}
      >
        <Box sx={{ ...card, overflow: "hidden" }}>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={2}
            alignItems={{ sm: "flex-start" }}
            justifyContent="space-between"
            sx={{ px: 2.5, py: 2 }}
          >
            <Box sx={{ minWidth: 0 }}>
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
              onChange={(_, next) => next && setKind(next)}
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
                  onOpen={() => entry.key !== openKey && pick(entry, true)}
                />
              ))}
            </Box>
          ))}
        </Box>
        <RunsUseNow data={data} env={env} />
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
