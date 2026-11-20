import PropTypes from "prop-types";
import { Box, MenuItem, Select, Stack, Typography } from "@mui/material";
import { isGitAgent, versionNameOf } from "../_mock/codeRefs";
import AgentFilesTab from "./AgentFilesTab";
import AgentMembersCard from "./AgentMembersCard";
import { memberAreasOf } from "./agentDefinitionSummary";
import {
  configRowsOf,
  identityRowsOf,
  isCodeAgent,
  reachRowsOf,
  snapshotLineOf,
  sourceRowsOf,
} from "./agentRecord";
import { MONO } from "./agentViewUtils";
import { mcpServersOfAgent } from "../_mock/mcpServers";
import { ActionText, InfoTip, KvRow, PanelCard } from "./AgentBits";
import { delegationEvidenceOf } from "../_mock/handoffs";

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
  agent,
  definition,
  source,
  files,
  prevFiles,
  label,
  prevLabel,
  latest,
  focusPath,
  diffOn,
  observeProject,
  observePending = false,
  onOpenEnv,
  onTab,
  protectedMembers,
  onVersion,
}) {
  const version = agent.versions.find((v) => v.label === label);
  const code = isCodeAgent(agent);
  const mcpServers = mcpServersOfAgent(agent);
  /* Every version reads vN; a code agent's carries its commit: "v5 · main @ 3f9a2c1" here, "v5 · 3f9a2c1" in the file tree. */
  const nameOf = (l) => versionNameOf(agent, l).main;
  const shortOf = (l) => versionNameOf(agent, l).short || l;
  const config = configRowsOf({ agent, definition, source });
  /* "v12 · in production", "v13 · latest draft" — what each version is, in the picker. */
  const versionTagOf = (l) => {
    const v = agent.versions.find((x) => x.label === l);
    const tag =
      l === agent.production
        ? "in production"
        : l === latest && !isGitAgent(agent)
          ? v?.pendingDraft
            ? "latest · draft"
            : "latest"
          : null;
    return `${nameOf(l)}${tag ? ` · ${tag}` : ""}`;
  };
  const memberAreas = memberAreasOf({ agent, definition, source });

  return (
    <Stack spacing={2}>
      {/* The definition belongs to a version — picked here, the same pick as the header's. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ flexWrap: "wrap", rowGap: 0.5 }}
      >
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Definition of
        </Typography>
        {onVersion ? (
          <Select
            size="small"
            value={label}
            onChange={(e) => onVersion(e.target.value)}
            renderValue={(v) => (
              <Box component="span" sx={{ fontFamily: MONO }}>
                {versionTagOf(v)}
              </Box>
            )}
            sx={{
              height: 28,
              minWidth: 150,
              "& .MuiSelect-select": { typography: "s2", py: 0.25 },
            }}
          >
            {[...agent.versions].reverse().map((v) => (
              <MenuItem
                key={v.label}
                value={v.label}
                sx={{ typography: "s2", fontFamily: MONO }}
              >
                {versionTagOf(v.label)}
              </MenuItem>
            ))}
          </Select>
        ) : (
          <Box
            component="span"
            sx={{ typography: "s2", fontFamily: MONO, color: "text.primary" }}
          >
            {versionTagOf(label)}
          </Box>
        )}
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`Read-only${source.kind === "endpoint" ? " · shows what the agent does, not its setup" : source.kind === "traces" ? " · read from its traces" : ` · read from ${source.label}`}`}
        </Typography>
      </Stack>

      {/* Everything read from the agent, in four short cards side by side:
          empty fields fold into one "Not set" line, and the name (the page
          title) isn't repeated. */}
      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" },
          alignItems: "start",
        }}
      >
        <Section
          title="Behaviour"
          note={definition?.members?.length ? "Shared by every member" : null}
          action={
            <ActionText onClick={() => onTab("constraints")}>
              What may change
            </ActionText>
          }
        >
          <DenseRows
            rows={config.filter(
              (r) => !USES.has(r.label) && r.label !== "Account",
            )}
          />
        </Section>

        <Section title="Tools and knowledge">
          <DenseRows
            rows={[
              ...config.filter((r) => USES.has(r.label)),
              ...(code
                ? [
                    {
                      label: "Snapshot",
                      value: snapshotLineOf({ definition, files }),
                      hint: `Prompts, tools and models found ${source.kind === "upload" ? "in that upload" : "at that commit"}.`,
                    },
                  ]
                : []),
              /* Tools it gets from MCP servers aren't in its config — the server lists them when it runs. */
              ...mcpServers.map(({ server, tools }) => ({
                id: server.id,
                label: server.label,
                value: `${tools.length} tools · ${tools.map((t) => t.name).join(", ")}`,
                hint: `MCP server · ${server.host} · ${server.transport.toUpperCase()}. Its tools are listed by the server when the agent runs, not written in its config.`,
              })),
            ]}
          />
        </Section>

        <Section title="Source">
          <DenseRows
            rows={[
              ...sourceRowsOf({ agent, source, definition, version }),
              ...config.filter((r) => r.label === "Account"),
            ]}
          />
        </Section>

        <Section
          title="Identity, reach and links"
          action={
            <ActionText onClick={() => onTab("connections")}>
              Edit on Connections
            </ActionText>
          }
        >
          <DenseRows
            rows={[
              ...identityRowsOf(agent).filter((r) => r.label !== "Name"),
              ...reachRowsOf(agent),
              {
                label: "Observe project",
                value: observeProject ? (
                  `${observeProject.name}${observePending ? " · waiting for its first trace" : " · linked"}`
                ) : (
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box component="span" sx={{ color: "text.subtitle" }}>
                      Not linked
                    </Box>
                    <ActionText onClick={() => onTab("connections")}>
                      Link a project
                    </ActionText>
                  </Stack>
                ),
              },
              {
                label: "Environments",
                value: agent.usedIn.length ? (
                  <Stack
                    direction="row"
                    flexWrap="wrap"
                    useFlexGap
                    columnGap={1.5}
                    rowGap={0.5}
                  >
                    {agent.usedIn.map((u) => (
                      <ActionText
                        key={u.envId}
                        onClick={() => onOpenEnv(u.envId)}
                      >
                        {u.envName}
                      </ActionText>
                    ))}
                  </Stack>
                ) : (
                  "None yet"
                ),
              },
            ]}
          />
        </Section>
      </Box>

      {definition?.members?.length > 0 && (
        <AgentMembersCard
          members={definition.members}
          areas={memberAreas}
          protectedIds={protectedMembers || []}
          onConstraints={() => onTab("constraints")}
          evidence={delegationEvidenceOf(agent)}
        />
      )}

      {files?.length > 0 && (
        <AgentFilesTab
          files={files}
          prevFiles={prevFiles}
          label={shortOf(label)}
          prevLabel={prevLabel ? shortOf(prevLabel) : prevLabel}
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
  /* Linked to a new project that hasn't had its first trace. */
  observePending: PropTypes.bool,
  onOpenEnv: PropTypes.func,
  onTab: PropTypes.func,
  protectedMembers: PropTypes.array,
  /* Picks the version shown — the same ?v= the header's picker sets. */
  onVersion: PropTypes.func,
};

function Section({ title, note, action, children }) {
  return (
    <PanelCard
      title={title}
      action={
        note || action ? (
          <Stack direction="row" alignItems="center" spacing={1.5}>
            {note && (
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle" }}
              >
                {note}
              </Typography>
            )}
            {action}
          </Stack>
        ) : null
      }
    >
      {children}
    </PanelCard>
  );
}
Section.propTypes = {
  title: PropTypes.node,
  note: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
};

/* Config areas that are what the agent can use, not how it behaves. */
const USES = new Set([
  "Tools",
  "Knowledge",
  "Skills",
  "Memory",
  "Variables",
  "Future AGI prompts",
]);
const EMPTY = new Set(["—", "-", "None", "None yet", ""]);
const isEmpty = (r) => typeof r.value === "string" && EMPTY.has(r.value.trim());

/* Short rows, no dividers: label and value on one line. Empty fields are
   listed once at the bottom ("Not set: Skills, Memory"), not as rows. */
function DenseRows({ rows }) {
  const shown = rows.filter((r) => !isEmpty(r));
  const unset = rows.filter(isEmpty).map((r) => r.label);
  return (
    <Box sx={{ px: 2, py: 1 }}>
      {shown.map((r) => (
        <Stack
          key={r.id || r.label}
          direction="row"
          spacing={1.5}
          alignItems="baseline"
          sx={{ py: 0.5 }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              width: 120,
              flexShrink: 0,
            }}
          >
            {r.label}
          </Typography>
          <Box
            sx={{ minWidth: 0, flex: 1, display: "flex", alignItems: "center" }}
          >
            <Typography
              component="div"
              sx={{
                typography: "s2",
                color: r.muted || r.hidden ? "text.subtitle" : "text.primary",
                fontFamily: r.mono ? MONO : undefined,
                wordBreak: r.mono ? "break-all" : "break-word",
                minWidth: 0,
              }}
            >
              {r.value}
            </Typography>
            <InfoTip text={r.hint || r.note || null} />
          </Box>
        </Stack>
      ))}
      {unset.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", pt: 0.5 }}>
          {`Not set: ${unset.join(", ")}`}
        </Typography>
      )}
    </Box>
  );
}
DenseRows.propTypes = { rows: PropTypes.array };

function Rows({ rows }) {
  return rows.map((r, i) => (
    <KvRow
      key={r.id || r.label}
      label={r.label}
      first={i === 0}
      hint={r.hint || r.note || null}
    >
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
