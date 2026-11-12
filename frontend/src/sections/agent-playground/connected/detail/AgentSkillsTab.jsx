import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  DialogContent,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ContentSkeleton from "src/components/content-skeleton";
import { ClosableDialog } from "src/components/custom-dialog";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import {
  isSkillsDisabled,
  useAgentSkillFiles,
  useAgentSkills,
} from "src/api/simulate-skills/skills";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import SkillFilesPanel from "./SkillFilesPanel";
import SkillsNotice from "./SkillsNotice";
import { INTEGRATE_TRACING } from "./skillsCopy";
import useAgentRoute from "./useAgentRoute";

const STATUS_TEXT = {
  no_version: "This agent has no version yet.",
  not_scanned: "This version's code hasn't been read for skills.",
  source_missing: "The uploaded folder for this version is no longer stored.",
  too_large: "The uploaded folder is too large to read for skills.",
};

const unsupportedText = (sourceKind) =>
  sourceKind === "github"
    ? "Skills from GitHub agents arrive soon. Agents uploaded as a folder show them here now."
    : "Skills are read from an agent's code, so only code agents have them.";

const PROBLEM_TEXT = {
  frontmatter_missing: "No frontmatter",
  frontmatter_invalid: "Frontmatter isn't valid YAML",
  frontmatter_name_invalid: "Name in frontmatter isn't valid",
  frontmatter_description_missing: "No description",
  not_text: "Not a text file",
  too_large: "Too large to read",
};

const problemLabel = (problem) =>
  PROBLEM_TEXT[problem] || problem.replace(/_/g, " ");

const RUNTIME_TEXT = {
  claude:
    "Read the way the Claude Agent SDK loads it: CLAUDE.md files, and AGENTS.md only when there is no CLAUDE.md.",
  codex:
    "Read the way Codex loads it: AGENTS.override.md or AGENTS.md in each folder.",
  generic: "Read from the AGENTS.md files on the path to the agent's folder.",
};

const CODEX_CAP_NOTE =
  "Codex reads up to 32 KiB of instructions, so the files after that limit are left out.";

const loadText = (file) => {
  const parts = [];
  if (file.imported_from) parts.push(`Imported by ${file.imported_from}`);
  parts.push(
    file.on_demand ? `When working in ${file.folder}/` : "Always loaded",
  );
  return parts.join(" · ");
};

const importDepth = (file, byPath) => {
  let depth = 0;
  let current = file;
  while (current?.imported_from && depth < 5) {
    depth += 1;
    current = byPath[current.imported_from];
  }
  return depth;
};

const USAGE_NOTE = {
  no_calls:
    "No calls with this version yet. Usage appears after its first run.",
  unavailable: "Skill usage can't be read right now. Try again shortly.",
};

const usageText = (item, usage) => {
  if (!item.readable) return problemLabel(item.problem);
  if (usage?.state !== "instrumented" || item.observed_calls == null)
    return "Not tracked";
  return `${usage.calls} calls · ${item.observed_calls} observed`;
};

function SectionTitle({ title, description }) {
  return (
    <Stack spacing={0.25} sx={{ mb: 1 }}>
      <Typography sx={{ typography: "m3" }}>{title}</Typography>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {description}
      </Typography>
    </Stack>
  );
}

SectionTitle.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
};

const itemShape = PropTypes.shape({
  id: PropTypes.string.isRequired,
  kind: PropTypes.string,
  name: PropTypes.string.isRequired,
  path: PropTypes.string.isRequired,
  description: PropTypes.string,
  version_number: PropTypes.number.isRequired,
  readable: PropTypes.bool.isRequired,
  problem: PropTypes.string,
  observed_calls: PropTypes.number,
});

function FilesDialog({ agentId, versionId, item, onClose }) {
  const { data, isLoading, isError, refetch } = useAgentSkillFiles(
    agentId,
    versionId,
    item?.id,
  );
  return (
    <ClosableDialog
      open={Boolean(item)}
      onClose={onClose}
      fullWidth
      maxWidth="md"
    >
      <DialogTitle sx={{ typography: "m1" }}>
        {item
          ? `${item.kind === "skill" ? item.name : item.path} v${item.version_number}`
          : ""}
      </DialogTitle>
      <DialogContent>
        {isLoading && <ContentSkeleton variant="panel" />}
        {isError && (
          <ErrorState title="Couldn't load the files" onRetry={refetch} />
        )}
        {data && (
          <SkillFilesPanel
            files={data.files}
            label={`Files in ${item?.path}`}
          />
        )}
      </DialogContent>
    </ClosableDialog>
  );
}

FilesDialog.propTypes = {
  agentId: PropTypes.string.isRequired,
  versionId: PropTypes.string,
  item: itemShape,
  onClose: PropTypes.func.isRequired,
};

function SkillRows({ items, usage, onOpen }) {
  return (
    <Table size="small">
      <TableHead>
        <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Skill</TableCell>
          <TableCell>Version</TableCell>
          <TableCell>Path</TableCell>
          <TableCell>Calls</TableCell>
          <TableCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id} hover sx={{ height: ROW_HEIGHT.default }}>
            <TableCell>
              <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                {item.name}
              </Typography>
              {item.description ? (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {item.description}
                </Typography>
              ) : null}
            </TableCell>
            <TableCell>{`v${item.version_number}`}</TableCell>
            <TableCell>
              <Box
                component="span"
                sx={{ fontFamily: "monospace", color: "text.secondary" }}
              >
                {item.path}
              </Box>
            </TableCell>
            <TableCell sx={{ color: "text.secondary" }}>
              {usageText(item, usage)}
            </TableCell>
            <TableCell align="right">
              <Button size="small" onClick={() => onOpen(item)}>
                Open files
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

SkillRows.propTypes = {
  items: PropTypes.arrayOf(itemShape).isRequired,
  usage: PropTypes.shape({
    state: PropTypes.string.isRequired,
    calls: PropTypes.number.isRequired,
  }),
  onOpen: PropTypes.func.isRequired,
};

const contextFileShape = PropTypes.shape({
  id: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  path: PropTypes.string.isRequired,
  version_number: PropTypes.number.isRequired,
  readable: PropTypes.bool.isRequired,
  problem: PropTypes.string,
  position: PropTypes.number.isRequired,
  on_demand: PropTypes.bool.isRequired,
  folder: PropTypes.string,
  imported_from: PropTypes.string,
});

function ContextRows({ files, onOpen }) {
  const byPath = Object.fromEntries(files.map((file) => [file.path, file]));
  return (
    <Table size="small">
      <TableHead>
        <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>File</TableCell>
          <TableCell>When it loads</TableCell>
          <TableCell>Version</TableCell>
          <TableCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {files.map((file) => (
          <TableRow key={file.id} hover sx={{ height: ROW_HEIGHT.default }}>
            <TableCell>
              <Box sx={{ pl: importDepth(file, byPath) * 2 }}>
                <Box
                  component="span"
                  sx={{ typography: "s1", fontFamily: "monospace" }}
                >
                  {file.path}
                </Box>
                {!file.readable && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {problemLabel(file.problem)}
                  </Typography>
                )}
              </Box>
            </TableCell>
            <TableCell sx={{ color: "text.secondary" }}>
              {loadText(file)}
            </TableCell>
            <TableCell>{`v${file.version_number}`}</TableCell>
            <TableCell align="right">
              <Button size="small" onClick={() => onOpen(file)}>
                Open file
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

ContextRows.propTypes = {
  files: PropTypes.arrayOf(contextFileShape).isRequired,
  onOpen: PropTypes.func.isRequired,
};

export default function AgentSkillsTab() {
  const { agentId, versionId } = useAgentRoute();
  const { data, isLoading, isError, error, refetch } = useAgentSkills(
    agentId,
    versionId,
  );
  const [opened, setOpened] = useState(null);

  if (isLoading) return <ContentSkeleton variant="rows" rows={4} />;
  if (isError && isSkillsDisabled(error))
    return (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="Skills aren't turned on"
        description="Ask an admin to enable them for this workspace."
      />
    );
  if (isError)
    return (
      <ErrorState title="Couldn't load this agent's skills" onRetry={refetch} />
    );
  if (data.status === "unsupported_source" || STATUS_TEXT[data.status])
    return (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="No skills to show"
        description={
          data.status === "unsupported_source"
            ? unsupportedText(data.source_kind)
            : STATUS_TEXT[data.status]
        }
      />
    );

  const skills = data.items.filter((item) => item.kind === "skill");
  const contextFiles = data.context?.files ?? [];
  const usage = data.usage;
  const versionNumber = data.agent_version?.version_number;

  return (
    <Stack spacing={3}>
      {usage?.state === "not_instrumented" && (
        <SkillsNotice icon="solar:info-circle-linear">
          {INTEGRATE_TRACING}
        </SkillsNotice>
      )}
      {USAGE_NOTE[usage?.state] && (
        <SkillsNotice icon="solar:clock-circle-linear">
          {USAGE_NOTE[usage.state]}
        </SkillsNotice>
      )}
      <Box>
        <SectionTitle
          title="Skills"
          description={`Read from agent v${versionNumber}'s code. A skill's version changes only when its files change.`}
        />
        {skills.length ? (
          <SkillRows items={skills} usage={usage} onOpen={setOpened} />
        ) : (
          <EmptyState
            icon="solar:lightbulb-bolt-linear"
            title="No skills in this version"
            description="Folders with a SKILL.md in the agent's code appear here."
          />
        )}
      </Box>
      {contextFiles.length > 0 && (
        <Box>
          <SectionTitle
            title="Agent context"
            description={`The instructions agent v${versionNumber} starts with, in the order they load. ${RUNTIME_TEXT[data.context.runtime] ?? ""}`}
          />
          {data.context.capped && (
            <Box sx={{ mb: 1 }}>
              <SkillsNotice icon="solar:info-circle-linear">
                {CODEX_CAP_NOTE}
              </SkillsNotice>
            </Box>
          )}
          <ContextRows files={contextFiles} onOpen={setOpened} />
        </Box>
      )}
      <FilesDialog
        agentId={agentId}
        versionId={versionId}
        item={opened}
        onClose={() => setOpened(null)}
      />
    </Stack>
  );
}
