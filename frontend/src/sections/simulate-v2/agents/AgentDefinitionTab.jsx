import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import AgentFilesTab from "./AgentFilesTab";
import AgentMembersCard from "./AgentMembersCard";
import { memberAreasOf } from "./agentDefinitionSummary";
import {
  configRowsOf, identityRowsOf, isCodeAgent, reachRowsOf, snapshotLineOf, sourceRowsOf,
} from "./agentRecord";
import { MONO } from "./agentViewUtils";
import { ActionText, KvRow, PanelCard } from "./AgentBits";

/**
 * Definition — a read-only view of the agent record (RSI PRD, "What Agents
 * stores for every agent"): Identity, Source, Reach, Configuration and
 * Links, for the version being viewed. For code, the version is a pointer
 * to a commit plus a snapshot of what we found there — not a copy of the
 * repository. Edits happen where they belong: identity and reach on
 * Connections, what may change on Constraints. Below the record, the files
 * it was read from, with what changed since the version before.
 */
export default function AgentDefinitionTab({
  agent, definition, source, files, prevFiles, label, prevLabel, latest, focusPath, diffOn, observeProject, onOpenEnv, onTab, protectedMembers,
}) {
  const version = agent.versions.find((v) => v.label === label);
  const code = isCodeAgent(agent);
  const config = configRowsOf({ agent, definition, source });
  const memberAreas = memberAreasOf({ agent, definition, source });

  return (
    <Stack spacing={2}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {"Read-only record of "}
        <Box component="span" sx={{ fontFamily: MONO, color: "text.primary" }}>{`${label}${label === latest ? " (latest)" : ""}`}</Box>
        {source.kind === "endpoint" ? " · an endpoint shows what the agent does, not how it's set up" : source.kind === "traces" ? " · read from its traces" : ` · read from ${source.label}`}
      </Typography>

      <Section title="Identity" action={<ActionText onClick={() => onTab("connections")}>Edit on Connections</ActionText>}>
        <Rows rows={identityRowsOf(agent)} />
      </Section>

      <Section title="Source">
        <Rows
          rows={[
            ...sourceRowsOf({ agent, source, definition, version }),
            ...(code ? [{ label: "Snapshot", value: snapshotLineOf({ definition, files }), hint: "The prompts, tools and models we found at that commit, kept with the version." }] : []),
          ]}
        />
      </Section>

      <Section title="Reach" action={<ActionText onClick={() => onTab("connections")}>Edit on Connections</ActionText>}>
        <Rows rows={reachRowsOf(agent)} />
      </Section>

      <Section
        title="Configuration"
        note={definition?.members?.length ? "Shared by every member" : null}
        action={<ActionText onClick={() => onTab("constraints")}>What may change</ActionText>}
      >
        <Rows rows={config} />
      </Section>

      {definition?.members?.length > 0 && (
        <AgentMembersCard members={definition.members} areas={memberAreas} protectedIds={protectedMembers || []} onConstraints={() => onTab("constraints")} />
      )}

      <Section title="Links">
        <KvRow label="Observe project" first>
          {observeProject ? (
            <Typography sx={{ typography: "s2" }}>
              {observeProject.name}
              <Box component="span" sx={{ color: "text.subtitle" }}> · linked</Box>
            </Typography>
          ) : (
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>Not linked</Typography>
              <ActionText onClick={() => onTab("connections")}>Link a project</ActionText>
            </Stack>
          )}
        </KvRow>
        <KvRow label="Environments">
          {agent.usedIn.length ? (
            <Stack direction="row" flexWrap="wrap" useFlexGap columnGap={1.5} rowGap={0.5}>
              {agent.usedIn.map((u) => (
                <ActionText key={u.envId} onClick={() => onOpenEnv(u.envId)}>{u.envName}</ActionText>
              ))}
            </Stack>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.subtitle" }}>None yet</Typography>
          )}
        </KvRow>
      </Section>

      {files?.length > 0 && (
        <AgentFilesTab
          files={files}
          prevFiles={prevFiles}
          label={label}
          prevLabel={prevLabel}
          sourceKind={source.kind}
          focusPath={focusPath}
          diffOn={diffOn}
        />
      )}
    </Stack>
  );
}

AgentDefinitionTab.propTypes = {
  agent: PropTypes.object.isRequired,
  definition: PropTypes.object,
  source: PropTypes.object.isRequired,
  files: PropTypes.array,
  prevFiles: PropTypes.array,
  label: PropTypes.string,
  prevLabel: PropTypes.string,
  latest: PropTypes.string,
  focusPath: PropTypes.string,
  diffOn: PropTypes.bool,
  observeProject: PropTypes.object,
  onOpenEnv: PropTypes.func,
  onTab: PropTypes.func,
  protectedMembers: PropTypes.array,
};

function Section({ title, note, action, children }) {
  return (
    <PanelCard
      title={title}
      action={(note || action) ? (
        <Stack direction="row" alignItems="center" spacing={1.5}>
          {note && <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{note}</Typography>}
          {action}
        </Stack>
      ) : null}
    >
      {children}
    </PanelCard>
  );
}
Section.propTypes = { title: PropTypes.node, note: PropTypes.node, action: PropTypes.node, children: PropTypes.node };

function Rows({ rows }) {
  return rows.map((r, i) => (
    <KvRow key={r.id || r.label} label={r.label} first={i === 0} hint={r.hint || r.note || null}>
      <Typography
        sx={{
          typography: "s2",
          color: r.muted || r.hidden ? "text.subtitle" : "text.primary",
          fontFamily: r.mono ? MONO : undefined,
          wordBreak: r.mono ? "break-all" : undefined,
        }}
      >
        {r.value}
      </Typography>
    </KvRow>
  ));
}
Rows.propTypes = { rows: PropTypes.array };
