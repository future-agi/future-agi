import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import StatusBadge from "src/components/status-badge";
import { RouterLink } from "src/routes/components";
import {
  CASE_ROLE,
  EVIDENCE_COPY,
  PART_LABELS,
  caseShowsDefault,
  cutAtWord,
  partGroups,
} from "./evidence.constants";

const PARTS = Object.keys(PART_LABELS);
// A part the source holds, whole or in part; any other status is missing.
const HELD = new Set(["present", "partial", "truncated"]);
const TITLE_CHARS = 90;
const PREVIEW_CHARS = 240;
const BODY = { typography: "s1", color: "text.secondary" };

// A case is named by what the user asked, as the person saw it in Observe.
const caseTitle = (item, kind) => {
  const turns = item.transcript?.turns ?? [];
  const first = turns.find((turn) => turn.role === "user") ?? turns[0];
  const text = (first?.text ?? "").replace(/\s+/g, " ").trim();
  return text ? cutAtWord(text, TITLE_CHARS) : EVIDENCE_COPY.untitled(kind);
};

function Label({ children }) {
  return (
    <Box component="span" sx={{ fontWeight: 500, color: "text.primary" }}>
      {children}
    </Box>
  );
}

Label.propTypes = { children: PropTypes.node };

function PartsLine({ label, item, parts }) {
  if (!parts.length) return null;
  return (
    <Typography sx={BODY}>
      <Label>{label}</Label> {partGroups(item, parts).join("; ")}
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
    <Typography sx={{ ...BODY, whiteSpace: "pre-wrap" }}>
      <Label>{turn.role === "user" ? "User" : "Agent"}:</Label>{" "}
      {cutAtWord(turn.text, PREVIEW_CHARS)}
    </Typography>
  );
}

Turn.propTypes = { turn: PropTypes.object.isRequired };

// Where a case came from, under its title: the Observe session or call, with a
// way to open it while Observe still keeps it.
function Origin({ item, kind, observeHref }) {
  if (!item.source_id || !kind) return null;
  return (
    <Typography
      noWrap
      component="div"
      sx={{ typography: "s3", color: "text.tertiary" }}
    >
      {EVIDENCE_COPY.observeSource(kind, item.source_id)}
      {observeHref && item.source_available ? (
        <>
          {" · "}
          <Link component={RouterLink} href={observeHref} underline="hover">
            {EVIDENCE_COPY.openInObserve}
          </Link>
        </>
      ) : null}
    </Typography>
  );
}

Origin.propTypes = {
  item: PropTypes.object.isRequired,
  kind: PropTypes.string,
  observeHref: PropTypes.string,
};

// One imported case: what its source holds and lacks, what became of it, and —
// while it waits — what the person wants its new scenario to be. A simulated
// source (a Simulator project's) says so; production sources are regression evidence.
export default function EvidenceCaseCard({
  item,
  kind,
  simulated,
  observeHref,
  shows,
  onShowsChange,
}) {
  const turns = item.transcript?.turns ?? [];
  const hidden = Math.max(turns.length - 2, 0);
  const [allTurns, setAllTurns] = useState(false);
  const held = PARTS.filter((part) => HELD.has(item[part]?.status));
  const missing = PARTS.filter((part) => !HELD.has(item[part]?.status));
  const choice = shows ?? caseShowsDefault(item);
  return (
    // A panel: flat, with a hairline border and the panel radius.
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        p: 1.5,
      }}
      data-testid={`evidence-case-${item.id}`}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            component="h3"
            noWrap
            sx={{ typography: "s1", fontWeight: 500, color: "text.primary" }}
          >
            {caseTitle(item, kind)}
          </Typography>
          <Origin item={item} kind={kind} observeHref={observeHref} />
        </Box>
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
        <Typography sx={{ ...BODY, mb: 1 }}>{item.reason}</Typography>
      ) : null}
      {!item.source_available ? (
        <Typography sx={{ ...BODY, mb: 1 }}>
          {EVIDENCE_COPY.provenanceUnavailable}
        </Typography>
      ) : null}
      {item.changed_since_import ? (
        <Typography
          sx={{ typography: "s1", color: "status.warning.fg", mb: 1 }}
        >
          {EVIDENCE_COPY.changed}
        </Typography>
      ) : null}
      <Stack spacing={0.25} sx={{ mb: 1 }}>
        <PartsLine label={EVIDENCE_COPY.has} item={item} parts={held} />
        <PartsLine label={EVIDENCE_COPY.missing} item={item} parts={missing} />
      </Stack>
      {/* The first and last turns; the ones between open on request. */}
      {(allTurns ? turns : turns.slice(0, 1)).map((turn, index) => (
        <Turn key={`${turn.trace_id}-${index}`} turn={turn} />
      ))}
      {hidden ? (
        <Link
          component="button"
          type="button"
          underline="hover"
          sx={{ typography: "s3", alignSelf: "flex-start" }}
          onClick={() => setAllTurns((open) => !open)}
        >
          {allTurns
            ? EVIDENCE_COPY.fewerTurns
            : EVIDENCE_COPY.moreTurns(hidden)}
        </Link>
      ) : null}
      {!allTurns && turns.length > 1 ? (
        <Turn turn={turns[turns.length - 1]} />
      ) : null}
      {item.status === "drafted" || item.status === "added" ? (
        <Box sx={{ mt: 1 }}>
          <Typography sx={{ typography: "s1", color: "text.primary" }}>
            {EVIDENCE_COPY.scenario}:{" "}
            <Label>{item.scenario_name || item.scenario_key}</Label>
          </Typography>
          {(item.new_checks ?? []).map((check) => (
            <Typography key={check.name} sx={BODY}>
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
  observeHref: PropTypes.string,
  shows: PropTypes.string,
  onShowsChange: PropTypes.func,
};
