import PropTypes from "prop-types";
import { Box, MenuItem, Stack, TextField, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge";
import {
  CASE_ROLE,
  EVIDENCE_COPY,
  PART_LABELS,
  caseShowsDefault,
  cutAtWord,
} from "./evidence.constants";

const PARTS = Object.keys(PART_LABELS);
// A part the source holds, whole or in part; any other status is missing.
const HELD = new Set(["present", "partial", "truncated"]);
const TITLE_CHARS = 90;
const PREVIEW_CHARS = 240;

// A case is named by what the user asked, as the person saw it in Observe.
const caseTitle = (item, kind) => {
  const turns = item.transcript?.turns ?? [];
  const first = turns.find((turn) => turn.role === "user") ?? turns[0];
  const text = (first?.text ?? "").replace(/\s+/g, " ").trim();
  return text ? cutAtWord(text, TITLE_CHARS) : EVIDENCE_COPY.untitled(kind);
};

// A part with its reason beside it. Step 2 explains the starting state, so
// its reason is not repeated on every case.
const partText = (item, part) => {
  const reason = part === "starting_state" ? "" : item[part]?.reason;
  return reason
    ? `${PART_LABELS[part]} (${reason.replace(/\.$/, "")})`
    : PART_LABELS[part];
};

function PartsLine({ label, item, parts }) {
  if (!parts.length) return null;
  return (
    <Typography variant="body2" color="text.secondary">
      <strong>{label}</strong>{" "}
      {parts.map((part) => partText(item, part)).join("; ")}
    </Typography>
  );
}

PartsLine.propTypes = {
  label: PropTypes.string.isRequired,
  item: PropTypes.object.isRequired,
  parts: PropTypes.arrayOf(PropTypes.string).isRequired,
};

function Turn({ turn }) {
  return (
    <Typography
      variant="body2"
      sx={{ whiteSpace: "pre-wrap", color: "text.secondary" }}
    >
      <strong>{turn.role === "user" ? "User" : "Agent"}:</strong>{" "}
      {cutAtWord(turn.text, PREVIEW_CHARS)}
    </Typography>
  );
}

Turn.propTypes = { turn: PropTypes.object.isRequired };

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
  const hidden = Math.max(turns.length - 2, 0);
  const held = PARTS.filter((part) => HELD.has(item[part]?.status));
  const missing = PARTS.filter((part) => !HELD.has(item[part]?.status));
  const choice = shows ?? caseShowsDefault(item);
  return (
    <Box
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
      data-testid={`evidence-case-${item.id}`}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="subtitle2" noWrap sx={{ flex: 1, minWidth: 0 }}>
          {caseTitle(item, kind)}
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
      <Stack spacing={0.25} sx={{ mb: 1 }}>
        <PartsLine label={EVIDENCE_COPY.has} item={item} parts={held} />
        <PartsLine label={EVIDENCE_COPY.missing} item={item} parts={missing} />
      </Stack>
      {turns.length ? <Turn turn={turns[0]} /> : null}
      {hidden ? (
        <Typography variant="body2" color="text.disabled">
          {EVIDENCE_COPY.moreTurns(hidden)}
        </Typography>
      ) : null}
      {turns.length > 1 ? <Turn turn={turns[turns.length - 1]} /> : null}
      {item.status === "drafted" || item.status === "added" ? (
        <Box sx={{ mt: 1 }}>
          <Typography variant="body2">
            {EVIDENCE_COPY.scenario}:{" "}
            <strong>{item.scenario_name || item.scenario_key}</strong>
          </Typography>
          {(item.new_checks ?? []).map((check) => (
            <Typography key={check.name} variant="body2" color="info.main">
              {EVIDENCE_COPY.newCheck}: {check.what || check.name}
            </Typography>
          ))}
        </Box>
      ) : null}
      {item.status === "pending" && onShowsChange ? (
        <TextField
          select
          size="small"
          label={EVIDENCE_COPY.shows}
          value={choice}
          onChange={(event) => onShowsChange(item.id, event.target.value)}
          helperText={EVIDENCE_COPY.showsDetail[choice || "unset"]}
          SelectProps={{ displayEmpty: true }}
          InputLabelProps={{ shrink: true }}
          fullWidth
          sx={{ mt: 1.5 }}
        >
          <MenuItem value="">{EVIDENCE_COPY.showsUnset}</MenuItem>
          {Object.entries(
            simulated
              ? EVIDENCE_COPY.showsOptionsSimulated
              : EVIDENCE_COPY.showsOptions,
          ).map(([value, label]) => (
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
