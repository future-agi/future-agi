import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Typography } from "@mui/material";

import { MODALITY } from "../../agentTypes";
import { AGENT_SHAPE } from "./agents.shapes";
import { AGENTS_PANEL_COPY } from "./agents.constants";
import AgentHeroCard from "./AgentHeroCard";
import VersionHistoryCard from "./VersionHistoryCard";
import AddAgentDrawer from "./AddAgentDrawer";
import {
  normalizeAgentVersions, applyActiveVersion, mintNextVersion, toEnvAgentVersion,
} from "./agentVersion.helpers";

// The composed Agents surface, hosted inside Overview's "Manage versions"
// drawer. One environment carries one source agent; this panel shows that agent
// (AgentHeroCard) and its version timeline (VersionHistoryCard), and owns the
// add-new-version drawer.
//
// Every write goes through the `patch` prop using the A1 store recipes. There is
// no backend: minting a version and switching the active one are local shape
// changes; the scripted "re-derivation" narration lives behind MOCK_DERIVATION
// in the helpers and is not wired here.
export default function AgentsPanel({ envState, patch, onNestedDrawerChange, locked = false }) {
  // Normalise so a legacy agent stored before versioning wraps as its own v1;
  // downstream cards read `versions[]` + `activeVersionId` unconditionally.
  const source = useMemo(() => normalizeAgentVersions(envState?.agent), [envState?.agent]);
  const [addingVersion, setAddingVersion] = useState(false);

  // Tell the host (Overview) when the nested Add-version drawer opens/closes so
  // it can close the outer drawer while this one is up (no stacking).
  useEffect(() => { onNestedDrawerChange?.(addingVersion); }, [addingVersion, onNestedDrawerChange]);

  // Switch the pinned version. applyActiveVersion mirrors the chosen version's
  // connection fields to the agent's top level, and the env-level pointer the
  // header + Overview read is kept in step via activeAgentVersion.
  const setActiveVersion = (versionId) => {
    if (!source) return;
    const target = (source.versions || []).find((v) => v.id === versionId);
    patch({
      agent: applyActiveVersion({ ...source, activeVersionId: versionId }),
      activeAgentVersion: target?.label,
    });
  };

  // Append a new version and make it active. agentVersions mirrors the source's
  // versions[] onto the env-level shape so the new label shows everywhere, not
  // just in this drawer.
  const addVersion = (record) => {
    if (!source) return;
    const next = mintNextVersion(source, record);
    const nextVersions = [...(source.versions || []), next];
    patch({
      agent: applyActiveVersion({
        ...source,
        versions: nextVersions,
        activeVersionId: next.id,
      }),
      agentVersions: nextVersions.map(toEnvAgentVersion),
      activeAgentVersion: next.label,
    });
    setAddingVersion(false);
  };

  if (!source) return null;

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 2 }}>
        <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
          {AGENTS_PANEL_COPY.title}
        </Typography>
        <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
          {AGENTS_PANEL_COPY.subtitle}
        </Typography>
      </Box>

      <AgentHeroCard agent={source} onAddVersion={() => setAddingVersion(true)} locked={locked} />

      <Box sx={{ mt: 2 }}>
        <VersionHistoryCard agent={source} onSetActiveVersion={setActiveVersion} locked={locked} />
      </Box>

      <AddAgentDrawer
        open={addingVersion}
        onClose={() => setAddingVersion(false)}
        agent={source}
        type={MODALITY[source.typeId]}
        onAdd={addVersion}
      />
    </Box>
  );
}

AgentsPanel.propTypes = {
  envState: PropTypes.shape({
    agent: AGENT_SHAPE,
    additionalAgents: PropTypes.arrayOf(PropTypes.any),
    activeAgentId: PropTypes.string,
  }),
  patch: PropTypes.func.isRequired,
  onNestedDrawerChange: PropTypes.func,
  locked: PropTypes.bool,
};
