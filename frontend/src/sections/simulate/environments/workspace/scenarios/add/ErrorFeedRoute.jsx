import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { formatDistanceToNowStrict } from "date-fns";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Chip,
  Collapse,
  IconButton,
  TextField,
  Select,
  MenuItem,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { useErrorFeedList } from "src/api/errorFeed/error-feed";
import ErrorSeverityBadge from "src/pages/dashboard/error-feed/components/ErrorSeverityBadge";
import ErrorStatusChip from "src/pages/dashboard/error-feed/components/ErrorStatusChip";
import SectionCard from "../../../components/SectionCard";
import EmptyState from "../../../components/EmptyState";
import SoonChip from "../../../components/SoonChip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import useUiSample from "../../../hooks/useUiSample";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";

// Most severe first, the Error Feed's own order.
const SEVERITIES = ["critical", "high", "medium", "low"];
const LIST_PARAMS = { limit: 100, sort_by: "last_seen", sort_dir: "desc" };

// The API proposes no scenarios from an issue yet. Under
// ?uiSample=scenarioSources every issue shows these, so Vel's design can be
// reviewed. Worded so they describe no real issue.
const SAMPLE_CASES = [
  {
    id: "sample-1",
    label: "Caller hurries the agent past the failing step",
    persona: "Sample caller · The Impatient Caller (inferred)",
    quote: "trace_sample_01  ·  trace_sample_02",
    traceCount: 6,
    personalData: 3,
    repro: { state: "reproduced", fails: 3, of: 3 },
  },
  {
    id: "sample-2",
    label: "Caller questions the figures the agent gives",
    persona: "Sample caller · The Sceptical Caller (inferred)",
    quote: "trace_sample_03",
    traceCount: 4,
    personalData: 2,
    repro: { state: "reproduced", fails: 2, of: 3 },
  },
  {
    id: "sample-3",
    label: "Caller repeats their details before asking",
    persona: "Sample caller · The Careful Caller (inferred)",
    quote: "trace_sample_04",
    traceCount: 2,
    personalData: 0,
    repro: { state: "not-reproduced", fails: 0, of: 3 },
  },
];
const SAMPLE_STEP =
  "Verify the step that failed in production before acting on it";

const REPRO_LABEL = {
  reproduced: "Reproduced",
  "not-reproduced": "Not reproduced",
};

const reproducible = (k) => k.repro.state === "reproduced";
const openCases = (issue) => issue.cases.filter(reproducible);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const lastSeenOf = (iso) => {
  if (!iso) return "";
  try {
    return `${formatDistanceToNowStrict(new Date(iso))} ago`;
  } catch {
    return "";
  }
};

// An issue in the shape the table reads. Only sample mode gives it cases.
const issueRow = (i, sample) => ({
  id: i.cluster_id,
  title: i.error?.name || i.cluster_id,
  type: i.error?.type || null,
  project: i.project || null,
  severity: i.severity,
  status: i.status,
  critical: i.severity === "critical",
  traces: i.trace_count ?? 0,
  occurrences: i.occurrences ?? 0,
  lastSeen: lastSeenOf(i.last_seen),
  cases: sample
    ? SAMPLE_CASES.map((k) => ({ ...k, id: `${i.cluster_id}:${k.id}` }))
    : [],
});

const SELECT_SX = {
  minWidth: 148,
  flexShrink: 0,
  "& .MuiSelect-select": { py: 0.75, pr: "28px !important", pl: 1.25 },
  "& .MuiSelect-icon": { color: "text.subtitle", right: 8 },
};

const capital = (v) => v.charAt(0).toUpperCase() + v.slice(1);

// Add scenarios from the Error Feed. The Error Feed groups failing production
// traces into issues; picking one adds the scenarios built from its traces,
// so a real-world failure becomes a test the environment runs. The issues are
// real; the proposals are not in the API yet, so they show as soon (or as
// Vel's sample under ?uiSample=scenarioSources) and approving saves nothing.
export default function ErrorFeedRoute({ onClose }) {
  const sample = useUiSample("scenarioSources");
  const { data, isLoading, isError } = useErrorFeedList(LIST_PARAMS, {
    retry: false,
  });

  const issues = useMemo(
    () => (data?.data || []).map((i) => issueRow(i, sample)),
    [data, sample],
  );

  const [severity, setSeverity] = useState("all");
  const [query, setQuery] = useState("");
  // { [issueId]: { [caseId]: true } }
  const [picked, setPicked] = useState({});

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return issues.filter((c) => {
      if (severity !== "all" && c.severity !== severity) return false;
      if (!q) return true;
      return `${c.id} ${c.title} ${c.type || ""}`.toLowerCase().includes(q);
    });
  }, [issues, severity, query]);
  const severities = SEVERITIES.filter((v) =>
    issues.some((c) => c.severity === v),
  );

  const pickedCases = (c) => openCases(c).filter((k) => picked[c.id]?.[k.id]);
  const chosen = shown
    .map((c) => ({ issue: c, cases: pickedCases(c) }))
    .filter((p) => p.cases.length > 0);
  const scenarioCount = chosen.reduce((n, p) => n + p.cases.length, 0);
  const importable = shown.filter((c) => openCases(c).length > 0);

  const allOf = (c) =>
    Object.fromEntries(openCases(c).map((k) => [k.id, true]));
  // The issue checkbox picks every scenario it has open, or clears them
  // when they're all picked already.
  const toggleIssue = (c) =>
    setPicked((p) => ({
      ...p,
      [c.id]: pickedCases(c).length === openCases(c).length ? {} : allOf(c),
    }));
  const toggleCase = (c, k) =>
    setPicked((p) => ({
      ...p,
      [c.id]: { ...p[c.id], [k.id]: !p[c.id]?.[k.id] },
    }));
  const selectAll = () => {
    const next = { ...picked };
    importable.forEach((c) => {
      next[c.id] = allOf(c);
    });
    setPicked(next);
  };

  // Nothing is sent: there is no endpoint that turns an issue into scenarios.
  const approve = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClose?.();
  };

  if (isLoading && !issues.length) {
    return (
      <SectionCard
        sx={{ flex: 1, display: "grid", placeItems: "center", minHeight: 240 }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <CircularProgress
            size={14}
            thickness={5}
            sx={{ color: "text.disabled" }}
          />
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            Loading Error Feed issues…
          </Typography>
        </Stack>
      </SectionCard>
    );
  }

  if (isError && !issues.length) {
    return (
      <SectionCard sx={{ flex: 1 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Couldn't load Error Feed issues"
          body="Try again in a moment."
        />
      </SectionCard>
    );
  }

  if (issues.length === 0) {
    return (
      <SectionCard sx={{ flex: 1 }}>
        <EmptyState
          icon="solar:bug-linear"
          title="No Error Feed issues yet"
          body="Issues show up here once the Error Feed groups failing traces from your projects."
        />
      </SectionCard>
    );
  }

  return (
    <SectionCard
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <TextField
          size="small"
          fullWidth
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search issues by id, title or error type…"
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <Box
                sx={{
                  pr: 0.75,
                  pl: 0.25,
                  display: "flex",
                  color: "text.subtitle",
                }}
              >
                <Iconify icon="solar:magnifer-linear" width={14} />
              </Box>
            ),
          }}
          sx={{ flex: 1, minWidth: 220 }}
        />
        {/* Vel scopes the list to this environment's agent's projects; the
            API has no agent to project link, so every project is listed. */}
        <SoonChip
          label="Agent scope"
          what="Scoping issues to the projects of the agent this environment tests"
          size="tiny"
        />
        <Select
          size="small"
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          inputProps={{ "aria-label": "Severity" }}
          IconComponent={(p) => (
            <Iconify {...p} icon="solar:alt-arrow-down-linear" width={12} />
          )}
          renderValue={(v) => (
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Iconify
                icon="solar:filter-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                {v === "all" ? "Any severity" : capital(v)}
              </Typography>
            </Stack>
          )}
          sx={SELECT_SX}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            Any severity
          </MenuItem>
          {severities.map((v) => (
            <MenuItem key={v} value={v} sx={{ typography: "s2" }}>
              {capital(v)}
            </MenuItem>
          ))}
        </Select>
        <UiOnlyFlag feature="scenarioSources" />
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={scenarioCount === 0}
          onClick={approve}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 148 }}
        >
          {scenarioCount > 0
            ? `Approve ${plural(scenarioCount, "scenario")}`
            : "Approve scenarios"}
        </Button>
      </Stack>

      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2.5,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {query || severity !== "all"
            ? `${shown.length} of ${issues.length} issues`
            : plural(issues.length, "issue")}
          {chosen.length > 0 && (
            <Box
              component="span"
              sx={{ color: "text.secondary", fontWeight: "fontWeightSemiBold" }}
            >
              {` · ${chosen.length} selected · ${plural(scenarioCount, "scenario")}`}
            </Box>
          )}
        </Typography>
        <Button
          size="small"
          onClick={selectAll}
          disabled={
            importable.length === 0 ||
            importable.every(
              (c) => pickedCases(c).length === openCases(c).length,
            )
          }
          sx={{
            color: "primary.main",
            minWidth: 0,
            "&.Mui-disabled": { color: "text.disabled" },
          }}
        >
          Select all
        </Button>
        {chosen.length > 0 && (
          <Button
            size="small"
            onClick={() => setPicked({})}
            sx={{ color: "text.secondary", minWidth: 0, ml: 0.5 }}
          >
            Clear
          </Button>
        )}
      </Stack>

      {sample && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            px: 2.5,
            py: 0.875,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          Each runs three times on today&apos;s agent. Only reproduced ones are
          added, with personal details replaced.
        </Typography>
      )}

      {shown.length === 0 ? (
        <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            No issues match your filters.
          </Typography>
        </Box>
      ) : (
        <IssuesTable
          rows={shown}
          sample={sample}
          picked={picked}
          onToggleIssue={toggleIssue}
          onToggleCase={toggleCase}
        />
      )}
    </SectionCard>
  );
}

ErrorFeedRoute.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func,
};

// `soon` columns are fields Vel shows that the list API doesn't send.
const COLUMNS = [
  { id: "issue", label: "Issue", minWidth: 320 },
  { id: "severity", label: "Severity", width: 100 },
  { id: "status", label: "Status", width: 120 },
  {
    id: "evidence",
    label: "Evidence",
    width: 110,
    soon: { label: "Evidence", what: "How sure the Error Feed is of an issue" },
  },
  { id: "traces", label: "Traces", width: 72, align: "right" },
  {
    id: "scenarios",
    label: "Scenarios",
    width: 92,
    align: "right",
    hint: "One per distinct situation in the issue's traces.",
    soon: {
      label: "Scenarios",
      what: "A count of the scenarios proposed from an issue",
    },
  },
  { id: "lastSeen", label: "Last seen", width: 110 },
];

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// Shaped like the Error Feed table (sticky compact header, checkbox column,
// two-line issue cell, severity dot badge, right-aligned trace count), so it
// reads as the same object the user sees in the Error Feed.
function IssuesTable({ rows, sample, picked, onToggleIssue, onToggleCase }) {
  return (
    <TableContainer sx={{ flex: 1, minHeight: 0 }}>
      <Table stickyHeader size="small" sx={{ minWidth: 980 }}>
        <TableHead>
          <TableRow
            sx={{
              "& .MuiTableCell-head": {
                bgcolor: "background.paper",
                backgroundImage: "none",
                // Sticky cells don't paint borders in a collapsed table, so a
                // shadow draws the divider.
                borderBottom: "none",
                boxShadow: (t) => `inset 0 -1px 0 ${t.palette.divider}`,
                py: 1.25,
                px: 1.5,
                whiteSpace: "nowrap",
              },
            }}
          >
            <TableCell padding="checkbox" sx={{ width: 40 }} />
            {COLUMNS.map((col) => (
              <TableCell
                key={col.id}
                align={col.align || "left"}
                sx={{ width: col.width, minWidth: col.minWidth }}
              >
                <ColumnHead col={col} sample={sample} />
              </TableCell>
            ))}
            <TableCell sx={{ width: 40 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((c) => (
            <IssueTableRow
              key={c.id}
              issue={c}
              sample={sample}
              pickedMap={picked[c.id] || {}}
              onToggle={() => onToggleIssue(c)}
              onToggleCase={(k) => onToggleCase(c, k)}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
IssuesTable.propTypes = {
  rows: PropTypes.array,
  sample: PropTypes.bool,
  picked: PropTypes.object,
  onToggleIssue: PropTypes.func,
  onToggleCase: PropTypes.func,
};

function ColumnHead({ col, sample }) {
  // Evidence is never in the API; the scenario count is, in sample mode, the
  // sample proposals' count.
  const soon = col.soon && !(sample && col.id === "scenarios");
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      justifyContent={col.align === "right" ? "flex-end" : "flex-start"}
    >
      {soon ? (
        <SoonChip label={col.soon.label} what={col.soon.what} size="tiny" />
      ) : (
        <Typography
          sx={{
            fontSize: 13,
            fontWeight: "fontWeightMedium",
            color: "text.secondary",
          }}
        >
          {col.label}
        </Typography>
      )}
      {col.hint && !soon && (
        <CustomTooltip show arrow size="small" title={col.hint} placement="top">
          <Box
            component="span"
            sx={{ display: "flex", color: "text.disabled", cursor: "help" }}
          >
            <Iconify icon="solar:info-circle-linear" width={14} />
          </Box>
        </CustomTooltip>
      )}
    </Stack>
  );
}
ColumnHead.propTypes = { col: PropTypes.object, sample: PropTypes.bool };

function IssueTableRow({ issue, sample, pickedMap, onToggle, onToggleCase }) {
  const [open, setOpen] = useState(false);
  const c = issue;

  const remaining = openCases(c);
  const noneReproduced = sample && remaining.length === 0;
  const pickedCount = remaining.filter((k) => pickedMap[k.id]).length;
  const allPicked = remaining.length > 0 && pickedCount === remaining.length;
  const somePicked = pickedCount > 0 && !allPicked;

  const checkbox = (
    <Checkbox
      size="small"
      checked={allPicked}
      indeterminate={somePicked}
      disabled={!sample || noneReproduced}
      onChange={onToggle}
      inputProps={{ "aria-label": `Pick ${c.title}` }}
      sx={CHECKBOX_SX}
    />
  );

  return (
    <>
      <TableRow
        hover
        selected={pickedCount > 0}
        // Clicking the row opens its detail; only the checkbox selects, so
        // reading an issue never quietly adds it.
        onClick={() => setOpen((o) => !o)}
        sx={{
          cursor: "pointer",
          height: 56,
          "&.Mui-selected, &.Mui-selected:hover": {
            bgcolor: "action.selected",
          },
          "& .MuiTableCell-body": {
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1.5,
            py: 0,
          },
        }}
      >
        <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
          {sample ? (
            checkbox
          ) : (
            <CustomTooltip
              show
              arrow
              size="small"
              title="Proposals from an issue aren't in the API yet, so there's nothing to pick."
            >
              <span>{checkbox}</span>
            </CustomTooltip>
          )}
        </TableCell>

        <TableCell>
          <Stack direction="column" spacing={0.5} justifyContent="center">
            <CustomTooltip
              show
              arrow
              size="small"
              title={c.title}
              placement="top-start"
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 460,
                }}
              >
                {c.title}
              </Typography>
            </CustomTooltip>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <FingerprintTag value={c.id} />
              {c.type && (
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    maxWidth: 200,
                  }}
                >
                  {c.type}
                </Typography>
              )}
              {c.project && (
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.375}
                  sx={{ color: "text.subtitle", minWidth: 0 }}
                >
                  <Iconify icon="solar:folder-linear" width={12} />
                  <Typography noWrap sx={{ typography: "s3", maxWidth: 180 }}>
                    {c.project}
                  </Typography>
                </Stack>
              )}
              {c.critical && (
                <Typography
                  sx={{
                    fontSize: "12px",
                    fontWeight: "fontWeightSemiBold",
                    letterSpacing: 0.4,
                    color: "error.main",
                  }}
                >
                  Critical
                </Typography>
              )}
              {sample && (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {noneReproduced
                    ? "· didn't reproduce"
                    : `· ${remaining.length} of ${c.cases.length} reproduced`}
                </Typography>
              )}
            </Stack>
          </Stack>
        </TableCell>

        <TableCell>
          <ErrorSeverityBadge severity={c.severity} />
        </TableCell>

        <TableCell>
          {c.status && <ErrorStatusChip status={c.status} />}
        </TableCell>

        {/* Evidence is soon: its header carries the chip. */}
        <TableCell />

        <TableCell align="right">
          <CustomTooltip
            show
            arrow
            size="small"
            title={`${plural(c.occurrences, "occurrence")} across ${plural(c.traces, "trace")}`}
          >
            <Typography
              component="span"
              sx={{
                typography: "s2",
                fontWeight: "fontWeightMedium",
                color: "text.primary",
                fontFeatureSettings: "'tnum'",
              }}
            >
              {c.traces}
            </Typography>
          </CustomTooltip>
        </TableCell>

        {/* "2 of 3" once a case is dropped, so the count never hides a
            partial pick. Blank in real mode: the header says soon. */}
        <TableCell align="right">
          {sample && (
            <Typography
              sx={{
                typography: "s2",
                fontWeight: "fontWeightMedium",
                fontFeatureSettings: "'tnum'",
                color: "text.primary",
              }}
            >
              {somePicked
                ? `${pickedCount} of ${remaining.length}`
                : remaining.length}
            </Typography>
          )}
        </TableCell>

        <TableCell>
          <Typography sx={{ typography: "s3", color: "text.disabled" }} noWrap>
            {c.lastSeen}
          </Typography>
        </TableCell>

        <TableCell>
          <IconButton
            size="small"
            aria-label={open ? "Hide detail" : "Show detail"}
            aria-expanded={open}
          >
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.subtitle" }}
            />
          </IconButton>
        </TableCell>
      </TableRow>

      {/* Full-width detail row, no border above so it reads as part of the
          issue row. */}
      <TableRow
        sx={{
          "& .MuiTableCell-body": {
            borderBottom: open ? "1px solid" : "none",
            borderColor: "divider",
            p: 0,
          },
        }}
      >
        <TableCell colSpan={COLUMNS.length + 2} sx={{ py: "0 !important" }}>
          <Collapse in={open} unmountOnExit>
            <Stack
              spacing={1.5}
              sx={{ px: 6, py: 2, bgcolor: "background.neutral" }}
            >
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Label sx={{ mb: 0 }}>Why it&apos;s in the feed</Label>
                <SoonChip
                  label="Reason"
                  what="Why the Error Feed raised an issue"
                  size="tiny"
                />
              </Stack>
              {sample ? (
                <SampleProposals
                  issue={c}
                  pickedMap={pickedMap}
                  onToggleCase={onToggleCase}
                />
              ) : (
                <Box>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.75}
                    sx={{ mb: 0.5 }}
                  >
                    <Label sx={{ mb: 0 }}>Proposed scenarios</Label>
                    <SoonChip
                      label="Proposals"
                      what="Scenarios proposed from an issue's failing traces"
                      plural
                      size="tiny"
                    />
                  </Stack>
                  <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                    {`Scenarios built from this issue's ${plural(c.traces, "failing trace")}, one per distinct situation, show up here to approve once the API proposes them.`}
                  </Typography>
                </Box>
              )}
              {/* The way back to the issue itself: its traces, trends and
                  analysis. */}
              <Box>
                <Button
                  size="small"
                  component={RouterLink}
                  to={paths.dashboard.errorFeed.detail(c.id)}
                  target="_blank"
                  rel="noopener"
                  endIcon={
                    <Iconify icon="solar:arrow-right-up-linear" width={14} />
                  }
                  sx={{ color: "text.secondary", ml: -0.75 }}
                >
                  {`Open ${c.id} in the Error Feed`}
                </Button>
              </Box>
            </Stack>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
IssueTableRow.propTypes = {
  issue: PropTypes.object,
  sample: PropTypes.bool,
  pickedMap: PropTypes.object,
  onToggle: PropTypes.func,
  onToggleCase: PropTypes.func,
};

function SampleProposals({ issue, pickedMap, onToggleCase }) {
  const c = issue;
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mb: 0.5 }}
      >
        <Label
          sx={{ mb: 0 }}
        >{`Becomes ${plural(c.cases.length, "scenario")}`}</Label>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          one per distinct situation across its traces
        </Typography>
        <UiOnlyFlag feature="scenarioSources" />
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        {c.cases.map((k, i) => (
          <CaseRow
            key={k.id}
            kase={k}
            first={i === 0}
            picked={!!pickedMap[k.id]}
            onToggle={() => onToggleCase(k)}
          />
        ))}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
        {`Each keeps the issue's goal, with the failing step as a critical sub-goal: “${SAMPLE_STEP}”. The caller's persona is inferred from the conversation.`}
      </Typography>
    </Box>
  );
}
SampleProposals.propTypes = {
  issue: PropTypes.object,
  pickedMap: PropTypes.object,
  onToggleCase: PropTypes.func,
};

// One scenario an issue becomes: the situation, the traces it was built from
// and how many traces it covers. Each can be dropped without dropping the
// issue. Only reproduced ones become targets.
function CaseRow({ kase, first, picked, onToggle }) {
  const blocked = !reproducible(kase);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 1,
        py: 0.75,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Checkbox
        size="small"
        checked={picked && !blocked}
        disabled={blocked}
        onChange={onToggle}
        inputProps={{ "aria-label": `Pick proposal ${kase.label}` }}
        sx={CHECKBOX_SX}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography
            noWrap
            sx={{
              typography: "s2",
              fontWeight: "fontWeightMedium",
              color: blocked ? "text.disabled" : "text.primary",
              minWidth: 0,
            }}
          >
            {kase.label}
          </Typography>
          <ReproTag repro={kase.repro} />
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0, mt: 0.25 }}
        >
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
          >
            {kase.persona}
          </Typography>
          <Tag icon={<Iconify icon="solar:shield-user-linear" width={12} />}>
            {kase.personalData
              ? `Personal data replaced · ${plural(kase.personalData, "value")}`
              : "No personal data found"}
          </Tag>
        </Stack>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontFamily: "ui-monospace, Menlo, monospace",
            color: "text.subtitle",
            mt: 0.25,
          }}
        >
          {kase.quote}
        </Typography>
      </Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          fontFeatureSettings: "'tnum'",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        {plural(kase.traceCount, "trace")}
      </Typography>
    </Stack>
  );
}
CaseRow.propTypes = {
  kase: PropTypes.object,
  first: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};

// Reproduced is the one that becomes a target; the other is said plainly.
function ReproTag({ repro }) {
  const reproduced = repro.state === "reproduced";
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={
        reproduced
          ? `Failed ${repro.fails} of ${repro.of} on today's agent. It becomes a target.`
          : "Passed every time on today's agent, so the failure didn't happen. Not a target."
      }
    >
      <Box sx={{ display: "inline-flex", cursor: "help" }}>
        <Tag strong={reproduced}>{REPRO_LABEL[repro.state]}</Tag>
      </Box>
    </CustomTooltip>
  );
}
ReproTag.propTypes = { repro: PropTypes.object };

function Tag({ children, icon, strong = false }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      icon={icon}
      label={children}
      sx={{
        flexShrink: 0,
        height: 20,
        fontSize: 11,
        "& .MuiChip-label": { px: 0.75 },
        ...(strong && { borderColor: "text.disabled", color: "text.primary" }),
      }}
    />
  );
}
Tag.propTypes = {
  children: PropTypes.node,
  icon: PropTypes.node,
  strong: PropTypes.bool,
};

// Same treatment as the Error Feed's error type tag (small neutral pill), so
// the two lists read alike.
function FingerprintTag({ value }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        height: 18,
        borderRadius: "4px",
        px: "5px",
        bgcolor: "action.selected",
        color: "text.secondary",
        maxWidth: 260,
        overflow: "hidden",
      }}
    >
      <Typography
        sx={{
          fontSize: "12px",
          fontWeight: "fontWeightMedium",
          fontFamily: "ui-monospace, Menlo, monospace",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
FingerprintTag.propTypes = { value: PropTypes.string };

function Label({ children, sx }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        color: "text.subtitle",
        letterSpacing: 0.4,
        mb: 0.5,
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node, sx: PropTypes.object };
