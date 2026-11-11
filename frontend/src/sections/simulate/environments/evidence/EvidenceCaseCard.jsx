import PropTypes from "prop-types";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge";
import CustomTooltip from "src/components/tooltip";
import {
  CASE_ROLE,
  EVIDENCE_COPY,
  PART_LABELS,
  PART_ROLE,
} from "./evidence.constants";

const PARTS = Object.keys(PART_LABELS);
const PREVIEW_CHARS = 240;

const clip = (text) =>
  text && text.length > PREVIEW_CHARS
    ? `${text.slice(0, PREVIEW_CHARS)}…`
    : text;

// One imported case: what its source holds and lacks, what became of it, and —
// while it waits — what the person wants its new scenario to be. A simulated
// source (a Simulator project's) says so; production sources are regression evidence.
export default function EvidenceCaseCard({
  item,
  kind,
  simulated,
  shows,
  onShowsChange,
}) {
  const turns = item.transcript?.turns ?? [];
  const preview =
    turns.length > 1 ? [turns[0], turns[turns.length - 1]] : turns;
  return (
    <Box
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
      data-testid={`evidence-case-${item.id}`}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="subtitle2" noWrap sx={{ flex: 1, minWidth: 0 }}>
          {item.source_id}
        </Typography>
        {simulated ? (
          <StatusBadge
            role="neutral"
            label={EVIDENCE_COPY.simulated(kind)}
            size="sm"
            variant="badge"
          />
        ) : null}
        <StatusBadge
          role={CASE_ROLE[item.status] || "neutral"}
          label={EVIDENCE_COPY[item.status] || item.status}
          size="sm"
        />
      </Stack>
      {item.reason ? (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {item.reason}
        </Typography>
      ) : null}
      {!item.source_available ? (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {EVIDENCE_COPY.provenanceUnavailable}
        </Typography>
      ) : null}
      {item.changed_since_import ? (
        <Typography variant="body2" color="warning.main" sx={{ mb: 1 }}>
          {EVIDENCE_COPY.changed}
        </Typography>
      ) : null}
      <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 1 }}>
        {PARTS.map((part) => (
          <CustomTooltip
            key={part}
            title={item[part]?.reason || ""}
            show={Boolean(item[part]?.reason)}
          >
            <span>
              <StatusBadge
                role={PART_ROLE[item[part]?.status] || "neutral"}
                label={`${PART_LABELS[part]}: ${item[part]?.status ?? "missing"}`}
                size="sm"
                variant="badge"
              />
            </span>
          </CustomTooltip>
        ))}
      </Stack>
      {preview.map((turn, index) => (
        <Typography
          key={`${turn.trace_id}-${index}`}
          variant="body2"
          sx={{ whiteSpace: "pre-wrap", color: "text.secondary" }}
        >
          <strong>{turn.role === "user" ? "User" : "Agent"}:</strong>{" "}
          {clip(turn.text)}
        </Typography>
      ))}
      {item.status === "drafted" || item.status === "added" ? (
        <Box sx={{ mt: 1 }}>
          <Typography variant="body2">
            {EVIDENCE_COPY.scenarioKey}: <code>{item.scenario_key}</code>
          </Typography>
          {(item.new_checks ?? []).map((check) => (
            <Typography key={check.name} variant="body2" color="info.main">
              {EVIDENCE_COPY.newCheck}: <strong>{check.name}</strong>
              {check.what ? ` — ${check.what}` : ""}
            </Typography>
          ))}
        </Box>
      ) : null}
      {item.status === "pending" && onShowsChange ? (
        <TextField
          select
          size="small"
          label={EVIDENCE_COPY.shows}
          value={shows}
          onChange={(event) => onShowsChange(item.id, event.target.value)}
          helperText={EVIDENCE_COPY.showsDetail[shows]}
          fullWidth
          sx={{ mt: 1.5 }}
        >
          {Object.entries(EVIDENCE_COPY.showsOptions).map(([value, label]) => (
            <MenuItem key={value} value={value}>
              {label}
            </MenuItem>
          ))}
        </TextField>
      ) : null}
    </Box>
  );
}

EvidenceCaseCard.propTypes = {
  item: PropTypes.object.isRequired,
  kind: PropTypes.oneOf(["session", "call"]),
  simulated: PropTypes.bool,
  shows: PropTypes.string,
  onShowsChange: PropTypes.func,
};
