import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Chip, Menu, MenuItem, Skeleton, Tooltip, Typography } from "@mui/material";
import { useQueries } from "@tanstack/react-query";
import axios, {
  traceSkillsEndpoint,
} from "src/utils/axios";
import Iconify from "src/components/iconify";
import {
  CONTENTS_MISMATCH,
  EVIDENCE,
  askedFor,
  failedLoadChip,
  versionName,
} from "src/sections/skills/runtime/runtimeCopy";
import {
  OBSERVE_SKILL_COPY,
  chipFilterItems,
  isUuid,
  skillVersionHref,
} from "../skillFilters";

// The Observe Skills column (SKILL-TRACES §7.1, UX-12, UX-04, lane ST9).
// Up to three chips per trace, then "+N". Loads come from endpoint 6, read
// once per list page for at most 100 traces (never the latest-value custom
// column path, which keeps one value per key and cannot show every skill a
// trace loaded). A failed load is a chip in the error tone.

export const SKILLS_COLUMN_ID = "skills";
export const MAX_SKILL_CHIPS = 3;
export const TRACE_SKILLS_PAGE_LIMIT = 100;

// ---------------------------------------------------------------------------
// Data: endpoint 6, page-scoped.

// The failure kinds of src/api/skills/runtime.js runtimeErrorKind, kept here
// so the trace list does not load the Skills API module.
export const traceSkillsErrorKind = (error) => {
  const code = error?.response?.data?.code || error?.code;
  const status = error?.response?.status;
  if (code === "project_not_readable" || status === 403) return "denied";
  if (code === "runtime_read_budget_exceeded") return "slow";
  if (["runtime_read_unavailable", "runtime_read_too_large"].includes(code)) {
    return "unavailable";
  }
  if (code === "invalid_runtime_query" || status === 400) return "invalid";
  return "error";
};

const NO_TRACES = Object.freeze({});
const NO_IDS = Object.freeze([]);
const uniqueTraceIds = (traceIds) =>
  [...new Set((traceIds || []).filter((id) => typeof id === "string" && id))].sort();
/** Endpoint 6 takes at most 100 trace ids; larger pages are read in chunks. */
export const chunkTraceIds = (ids, size = TRACE_SKILLS_PAGE_LIMIT) => {
  const chunks = [];
  for (let index = 0; index < ids.length; index += size) chunks.push(ids.slice(index, index + size));
  return chunks;
};

/** Validate one trace entry of the endpoint-6 response; unknown shapes read as no loads. */
export function mapTraceSkillsEntry(entry) {
  if (!entry || typeof entry !== "object") return { skills: [], failed: [], more: 0 };
  const skills = (Array.isArray(entry.skills) ? entry.skills : [])
    .filter((skill) => skill && typeof skill === "object")
    .map((skill) => ({
      skill_id: isUuid(skill.skill_id) ? skill.skill_id : null,
      name: typeof skill.name === "string" && skill.name ? skill.name : null,
      version_id: isUuid(skill.version_id) ? skill.version_id : null,
      version: Number.isInteger(skill.version) ? skill.version : null,
      requested_label: typeof skill.requested_label === "string" ? skill.requested_label : null,
      ...(Array.isArray(skill.agent_key) && skill.agent_key.length === 3 && skill.agent_key.every((part) => typeof part === "string")
        ? { agent_key: skill.agent_key } : {}),
      agent: typeof skill.agent === "string" ? skill.agent : null,
      evidence: typeof skill.evidence === "string" ? skill.evidence : null,
      loads: Number.isFinite(skill.loads) ? skill.loads : null,
      digest_status: skill.digest_status === "mismatch" ? "mismatch" : "verified",
    }));
  const failed = (Array.isArray(entry.failed) ? entry.failed : [])
    .filter((item) => item && typeof item.skill_name === "string")
    .map((item) => ({ skill_name: item.skill_name, error_type: typeof item.error_type === "string" ? item.error_type : null }));
  const more = Number.isInteger(entry.more) && entry.more > 0 ? entry.more : 0;
  return { skills, failed, more };
}

async function readTraceSkills({ projectId, ids, signal }) {
  const url = traceSkillsEndpoint();
  const response = await axios.post(url, { projection: "page", project_id: projectId, trace_ids: ids }, { signal });
  const traces = response?.data?.result?.traces ?? response?.data?.traces ?? {};
  // Only ids that were asked for are kept; a trace with no loads is absent
  // from the response and reads as "no loads" because it was requested.
  const asked = new Set(ids);
  return Object.fromEntries(
    Object.entries(traces)
      .filter(([traceId]) => asked.has(traceId))
      .map(([traceId, entry]) => [traceId, mapTraceSkillsEntry(entry)]),
  );
}

function combineTraceSkills(results) {
  if (!results.length) return { status: "idle", byTrace: NO_TRACES, errorKind: null, refetch: null };
  const failed = results.find((result) => result.isError);
  const pending = results.some((result) => result.isPending);
  const byTrace = Object.assign({}, ...results.map((result) => result.data || {}));
  return {
    status: failed ? "error" : pending ? "loading" : "ready",
    byTrace,
    errorKind: failed ? traceSkillsErrorKind(failed.error) : null,
    refetch: () => Promise.all(results.map((result) => result.refetch())),
    // Changes whenever any chunk settles or refreshes, for change detection.
    version: results.map((result) => `${result.status}:${result.dataUpdatedAt}:${result.errorUpdatedAt}`).join("|"),
  };
}

/**
 * Hydrate the Skills column for the traces shown in the grid (endpoint 6),
 * at most 100 ids per request. Returns
 * `{ status: "idle"|"loading"|"error"|"ready", byTrace, requested, errorKind, retry, version }`.
 * `requested` lists the ids this result covers: a shown trace outside it is
 * still loading, never "no loads".
 */
export function useTracePageSkills({ projectId, traceIds, enabled = true }) {
  const idsKey = JSON.stringify(uniqueTraceIds(traceIds));
  const ids = useMemo(() => JSON.parse(idsKey), [idsKey]);
  const active = Boolean(enabled && projectId && ids.length);
  const chunks = useMemo(() => (active ? chunkTraceIds(ids) : []), [active, ids]);
  const combined = useQueries({
    queries: chunks.map((chunk) => ({
      queryKey: ["observe", "trace-skills", projectId, chunk],
      queryFn: ({ signal }) => readTraceSkills({ projectId, ids: chunk, signal }),
      retry: false,
      staleTime: 30 * 1000,
      meta: { errorHandled: true },
    })),
    combine: combineTraceSkills,
  });
  const requested = active ? ids : NO_IDS;
  return {
    status: combined.status,
    byTrace: combined.byTrace,
    requested,
    errorKind: combined.errorKind,
    retry: combined.refetch,
    version: `${idsKey}#${combined.version || ""}`,
  };
}

/**
 * Renders nothing; runs `useTracePageSkills` and reports its result to
 * `onChange`. The grid mounts it only while the Skills column is shown, so
 * no read (and no React Query dependency) exists for anyone else.
 */
export function TracePageSkillsProbe({ projectId, traceIds, onChange }) {
  const result = useTracePageSkills({ projectId, traceIds });
  const latest = useRef(result);
  latest.current = result;
  // Report only when the request or a response changes, never per render.
  useEffect(() => {
    const { status, byTrace, requested, errorKind, retry } = latest.current;
    onChange({ status, byTrace, requested, errorKind, retry });
  }, [result.version, onChange]);
  useEffect(() => () => onChange(null), [onChange]);
  return null;
}

TracePageSkillsProbe.propTypes = {
  projectId: PropTypes.string,
  traceIds: PropTypes.array,
  onChange: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Presentation model (pure, tested).

/** Chips for one trace: loaded skills first (server order = first load), then failed loads. */
export function skillsCellModel(entry, max = MAX_SKILL_CHIPS) {
  const { skills, failed, more } = mapTraceSkillsEntry(entry);
  const all = [
    ...skills.map((skill) => ({
      kind: "loaded",
      key: `${skill.version_id || skill.name}:${skill.agent || ""}`,
      label: `${skill.name || OBSERVE_SKILL_COPY.unknownSkill} ${versionName(skill.version)}`,
      tooltip: [
        skill.requested_label ? askedFor(skill.requested_label) : null,
        skill.agent || null,
        skill.evidence ? EVIDENCE[skill.evidence] || null : null,
        skill.digest_status === "mismatch" ? CONTENTS_MISMATCH : null,
      ].filter(Boolean).join(" · "),
      skill,
    })),
    ...failed.map((item, index) => ({
      kind: "failed",
      key: `failed:${item.skill_name}:${index}`,
      label: failedLoadChip(item.skill_name),
      tooltip: failedLoadChip(item.skill_name, item.error_type || "other"),
      skill: null,
    })),
  ];
  const visible = all.slice(0, max);
  return { visible, hidden: all.length - visible.length + more, empty: all.length === 0 && more === 0 };
}

// ---------------------------------------------------------------------------
// Cell renderer.

function SkillChip({ chip, onAddFilter }) {
  const [menuAnchor, setMenuAnchor] = useState(null);
  const failed = chip.kind === "failed";
  const href = chip.skill ? skillVersionHref(chip.skill.skill_id, chip.skill.version) : null;
  const canFilter = Boolean(!failed && onAddFilter && chip.skill?.version_id);
  return (
    <>
      <Tooltip title={chip.tooltip || chip.label} placement="top" arrow>
        <Chip
          size="small"
          data-skill-chip={chip.kind}
          variant={failed ? "outlined" : "filled"}
          color={failed ? "error" : "default"}
          label={chip.label}
          clickable={canFilter}
          onClick={
            canFilter
              ? (event) => {
                  event.stopPropagation();
                  onAddFilter(chip.skill);
                }
              : undefined
          }
          onDelete={
            href
              ? (event) => {
                  event?.stopPropagation?.();
                  setMenuAnchor(event?.currentTarget?.closest?.("[data-skill-chip]") || event?.currentTarget || null);
                }
              : undefined
          }
          deleteIcon={href ? (
            <Iconify icon="solar:alt-arrow-down-linear" width={12} aria-label={OBSERVE_SKILL_COPY.moreFor(chip.label)} />
          ) : undefined}
          sx={{
            maxWidth: 180,
            bgcolor: failed ? "transparent" : "background.neutral",
            "& .MuiChip-label": { px: 0.75, overflow: "hidden", textOverflow: "ellipsis" },
          }}
        />
      </Tooltip>
      {href && (
        <Menu
          anchorEl={menuAnchor}
          open={Boolean(menuAnchor)}
          onClose={() => setMenuAnchor(null)}
          onClick={(event) => event.stopPropagation()}
        >
          {canFilter && (
            <MenuItem
              onClick={() => {
                setMenuAnchor(null);
                onAddFilter(chip.skill);
              }}
            >
              {OBSERVE_SKILL_COPY.addAsFilter}
            </MenuItem>
          )}
          <MenuItem component={RouterLink} to={href} onClick={() => setMenuAnchor(null)}>
            {OBSERVE_SKILL_COPY.openSkillVersion}
          </MenuItem>
        </Menu>
      )}
    </>
  );
}

SkillChip.propTypes = {
  chip: PropTypes.object.isRequired,
  onAddFilter: PropTypes.func,
};

/**
 * AG Grid cell renderer. Reads the page hydration from the grid `context`
 * (`traceSkills: {status, byTrace}`) and adds a filter through
 * `context.onAddSkillFilters(items)`.
 */
export default function SkillsCell({ data, context }) {
  const traceId = data?.trace_id;
  const hydration = context?.traceSkills;
  const requested =
    !traceId || !Array.isArray(hydration?.requested) || hydration.requested.includes(traceId);
  // A shown trace outside the last request is waiting for the next one.
  const status = requested ? hydration?.status ?? "idle" : "loading";
  const entry = traceId ? hydration?.byTrace?.[traceId] : undefined;
  const model = useMemo(() => skillsCellModel(entry), [entry]);
  const onAddFilter = context?.onAddSkillFilters
    ? (skill) => context.onAddSkillFilters((existing) => chipFilterItems(skill, existing))
    : undefined;

  if (status === "loading" && !entry) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
        <Skeleton variant="rounded" width={96} height={18} aria-label={OBSERVE_SKILL_COPY.loadingSkills} />
      </Box>
    );
  }
  if (status === "error" && !entry) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
        <Tooltip title={OBSERVE_SKILL_COPY.skillsUnavailable} placement="top" arrow>
          <Typography variant="caption" color="text.secondary" aria-label={OBSERVE_SKILL_COPY.skillsUnavailable}>
            —
          </Typography>
        </Tooltip>
      </Box>
    );
  }
  if (model.empty) return null;
  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 0.5, height: "100%", px: 1, minWidth: 0, overflow: "hidden" }}
      aria-label={OBSERVE_SKILL_COPY.skillsColumn}
    >
      {model.visible.map((chip) => (
        <SkillChip key={chip.key} chip={chip} onAddFilter={onAddFilter} />
      ))}
      {model.hidden > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
          {OBSERVE_SKILL_COPY.moreChips(model.hidden)}
        </Typography>
      )}
    </Box>
  );
}

SkillsCell.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

// Resolve agent identity from the load's full (project, service, agent) key;
// two services can use the same display name, even within the same trace.
export function SkillAgentCell({ data, value, context }) {
  const skills = context?.traceSkills?.byTrace?.[data?.trace_id]?.skills || [];
  const agents = new Map(skills.filter((skill) => skill.agent_key && skill.agent)
    .map((skill) => [JSON.stringify(skill.agent_key), skill]));
  if (!agents.size) return value ?? null;
  return Array.from(agents, ([key, skill]) => (
    <Button key={key} size="small" data-skill-agent="" onClick={() => context.onOpenAgent(skill)}>
      {skill.agent}
    </Button>
  ));
}
SkillAgentCell.propTypes = { data: PropTypes.object, value: PropTypes.string, context: PropTypes.object };

/** The AG Grid column definition for the Skills column (used by TraceGrid). */
export const skillsColumnDef = (column) => ({
  colId: SKILLS_COLUMN_ID,
  field: SKILLS_COLUMN_ID,
  headerName: column?.name || OBSERVE_SKILL_COPY.skillsColumn,
  hide: column?.isVisible === false,
  minWidth: 200,
  flex: 1,
  sortable: false,
  cellRenderer: SkillsCell,
  context: { sourceColumn: column },
});
