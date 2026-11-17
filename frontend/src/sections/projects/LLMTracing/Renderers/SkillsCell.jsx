import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Chip, Menu, MenuItem, Skeleton, Tooltip, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios, { traceSkillsEndpoint } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { runtimeErrorKind } from "src/api/skills/runtime";
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

const NO_TRACES = Object.freeze({});
const traceIdsKey = (traceIds) =>
  [...new Set((traceIds || []).filter((id) => typeof id === "string" && id))]
    .slice(0, TRACE_SKILLS_PAGE_LIMIT)
    .sort();

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

/**
 * Hydrate the Skills column for the traces on one list page (endpoint 6).
 * Returns `{ status: "idle"|"loading"|"error"|"ready", byTrace, errorKind, retry }`.
 */
export function useTracePageSkills({ projectId, traceIds, enabled = true }) {
  const ids = useMemo(() => traceIdsKey(traceIds), [traceIds]);
  const active = Boolean(enabled && projectId && ids.length);
  const query = useQuery({
    queryKey: ["observe", "trace-skills", projectId, ids],
    queryFn: async ({ signal }) => {
      const url = traceSkillsEndpoint("pageSkills");
      const response = await axios.post(url, { project_id: projectId, trace_ids: ids }, { signal });
      const traces = response?.data?.result?.traces ?? response?.data?.traces ?? {};
      return Object.fromEntries(
        Object.entries(traces).map(([traceId, entry]) => [traceId, mapTraceSkillsEntry(entry)]),
      );
    },
    enabled: active,
    retry: false,
    staleTime: 30 * 1000,
    meta: { errorHandled: true },
  });
  let status = "idle";
  if (active && query.isPending) status = "loading";
  else if (query.isError) status = "error";
  else if (query.data) status = "ready";
  return {
    status,
    byTrace: query.data || NO_TRACES,
    errorKind: query.isError ? runtimeErrorKind(query.error) : null,
    retry: query.refetch,
  };
}

/**
 * Renders nothing; runs `useTracePageSkills` and reports its result to
 * `onChange`. The grid mounts it only while the Skills column is shown, so
 * no read (and no React Query dependency) exists for anyone else.
 */
export function TracePageSkillsProbe({ projectId, traceIds, onChange }) {
  const { status, byTrace, errorKind, retry } = useTracePageSkills({ projectId, traceIds });
  useEffect(() => {
    onChange({ status, byTrace, errorKind, retry });
  }, [status, byTrace, errorKind, retry, onChange]);
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
            <Iconify icon="solar:alt-arrow-down-linear" width={12} aria-label={`More for ${chip.label}`} />
          ) : undefined}
          sx={{
            maxWidth: 180,
            height: 22,
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
  const status = hydration?.status ?? "idle";
  const entry = traceId ? hydration?.byTrace?.[traceId] : undefined;
  const model = useMemo(() => skillsCellModel(entry), [entry]);
  const onAddFilter = context?.onAddSkillFilters
    ? (skill) => context.onAddSkillFilters((existing) => chipFilterItems(skill, existing))
    : undefined;

  if (status === "loading" && !entry) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
        <Skeleton variant="rounded" width={96} height={18} aria-label="Loading skills" />
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
