import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSkill, useSkillVersions } from "src/api/skills";
import { useContextDetail } from "src/api/skills/context-authoring";
import { useSkillComparison } from "src/api/skills/runtime";
import { buildSkillTraceLink, runtimeTabUrl } from "src/sections/skills/utils/traces";
import { versionName } from "src/sections/skills/runtime/runtimeCopy";
import { OBSERVE_SKILL_COPY, comparisonMetricKey, sourceMetricPhrase } from "./skillFilters";

// The Observe source bar (SKILL-TRACES §7.1, UX-02, lane ST9). A link from a
// skill or context surface carries a `skillSource` parameter; this bar says
// what the list is ("refund-policy v4 · refund-agent · failed requests · 24 h
// after production moved to v4"), links back, and offers the matching
// before/after link. It is presentational: it never changes the filters, and
// the list view hides it as soon as the person edits them.

const COPY = OBSERVE_SKILL_COPY.sourceBar;

/** "24h" → "24 h", "7d" → "7 days". */
export const windowWords = (value) => {
  const match = /^([1-9]\d{0,2})(h|d)$/.exec(String(value || ""));
  if (!match) return "24 h";
  const count = Number(match[1]);
  return match[2] === "h" ? `${count} h` : `${count} ${count === 1 ? "day" : "days"}`;
};

const agentName = (agentKey) => {
  if (!Array.isArray(agentKey)) return null;
  return agentKey[2] || agentKey[1] || null;
};

/**
 * Text of the bar from resolved names. Pure, so it can be tested without the
 * reads. `move` is the endpoint-3 comparison (camelCased) or null.
 */
export function sourceBarText({ source, skillName, version, contextName, move }) {
  const subject = source.skillId
    ? [skillName || OBSERVE_SKILL_COPY.unknownSkill, version != null ? versionName(version) : null].filter(Boolean).join(" ")
    : contextName || OBSERVE_SKILL_COPY.context;
  const parts = [subject, agentName(source.agentKey), sourceMetricPhrase(source.metric)];
  if (move && source.side) {
    const words = { window: windowWords(source.window || "24h"), label: move.label || "production", toVersion: move.toVersion };
    parts.push(source.side === "before" ? COPY.beforeMove(words) : COPY.afterMove(words));
  }
  return parts.filter(Boolean).join(" · ");
}

/** The matching other-side link from the endpoint-3 response, or null. */
export function otherSideLink({ source, move }) {
  if (!move || !source?.side || !move.links) return null;
  const other = source.side === "after" ? "before" : "after";
  const key = comparisonMetricKey(source.metric);
  const link = move.links?.[other]?.[key];
  if (!link) return null;
  const href = buildSkillTraceLink({ link, kind: "all" });
  if (!href) return null;
  const version = other === "before" ? move.fromVersion : move.toVersion;
  return {
    href,
    label: other === "before" ? COPY.compareBefore(version) : COPY.compareAfter(version),
  };
}

export default function SkillSourceBar({ source, sortNotApplied = false, linkUnreadable = false }) {
  const skillId = source?.skillId ?? null;
  const contextId = source?.contextId ?? null;
  const skill = useSkill(skillId);
  const versions = useSkillVersions(skillId, { pageSize: 100 });
  const context = useContextDetail(contextId, { enabled: Boolean(contextId && !skillId) });
  const comparison = useSkillComparison(skillId, {
    eventId: source?.eventId ?? undefined,
    window: source?.window || "24h",
    enabled: Boolean(skillId && source?.eventId && source?.side),
  });

  if (!source && !linkUnreadable) return null;

  const notices = [
    linkUnreadable ? COPY.linkUnreadable : null,
    source && sortNotApplied ? COPY.sortNotApplied : null,
  ].filter(Boolean);

  if (!source) {
    return (
      <Box role="status" sx={barSx}>
        {notices.map((notice) => (
          <Typography key={notice} variant="caption" color="warning.dark">
            {notice}
          </Typography>
        ))}
      </Box>
    );
  }

  const version = source.versionId
    ? versions.data?.results?.find((item) => item.id === source.versionId)?.number ?? null
    : null;
  const skillName = skill.data?.name ?? null;
  const contextName = context.data?.context?.name ?? null;
  const move = comparison.isError ? null : comparison.data ?? null;
  const resolving = (skillId && skill.isPending && skill.fetchStatus !== "idle") ||
    (contextId && !skillId && context.isPending && context.fetchStatus !== "idle");
  const text = sourceBarText({ source, skillName, version, contextName, move });
  const backHref = skillId
    ? runtimeTabUrl({ kind: "skill", id: skillId, eventId: source.eventId, window: source.window || "24h" })
    : runtimeTabUrl({ kind: "agent_context", id: contextId });
  const backName = skillId ? skillName : contextName;
  const switchLink = otherSideLink({ source, move });

  return (
    <Box role="region" aria-label={COPY.label} sx={barSx}>
      <Stack direction="row" spacing={1} alignItems="center" useFlexGap sx={{ flexWrap: "wrap", minWidth: 0 }}>
        <Iconify icon="solar:book-bookmark-linear" width={16} sx={{ color: "text.secondary", flexShrink: 0 }} />
        {resolving ? (
          <Skeleton variant="text" width={240} aria-label={COPY.label} />
        ) : (
          <Typography variant="s2" sx={{ fontWeight: 500, overflowWrap: "anywhere", minWidth: 0 }}>
            {text}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {switchLink && (
          <Button size="small" variant="outlined" component={RouterLink} to={switchLink.href}>
            {switchLink.label}
          </Button>
        )}
        {backHref && (
          <Button
            size="small"
            variant="text"
            component={RouterLink}
            to={backHref}
            startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          >
            {COPY.backTo(backName || (skillId ? OBSERVE_SKILL_COPY.fields["skill.id"].toLowerCase() : OBSERVE_SKILL_COPY.context.toLowerCase()))}
          </Button>
        )}
      </Stack>
      {notices.map((notice) => (
        <Typography key={notice} variant="caption" color="warning.dark">
          {notice}
        </Typography>
      ))}
    </Box>
  );
}

const barSx = {
  display: "flex",
  flexDirection: "column",
  gap: 0.5,
  px: 2,
  py: 1,
  borderBottom: "1px solid",
  borderColor: "divider",
  bgcolor: "background.neutral",
  minWidth: 0,
};

SkillSourceBar.propTypes = {
  source: PropTypes.shape({
    skillId: PropTypes.string,
    versionId: PropTypes.string,
    contextId: PropTypes.string,
    bindingId: PropTypes.string,
    eventId: PropTypes.string,
    metric: PropTypes.string,
    side: PropTypes.oneOf(["before", "after", null]),
    agentKey: PropTypes.array,
    window: PropTypes.string,
  }),
  sortNotApplied: PropTypes.bool,
  linkUnreadable: PropTypes.bool,
};
