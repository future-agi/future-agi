import React, { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  DialogContent,
  DialogTitle,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import ContentSkeleton from "src/components/content-skeleton";
import { ClosableDialog } from "src/components/custom-dialog";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import {
  isSkillsDisabled,
  useAttachedSkills,
  useCodeSkillFiles,
  useCodeSkills,
  useCopyCodeSkill,
} from "src/api/simulate-skills/skills";
import { paths } from "src/routes/paths";
import SkillFilesPanel from "src/sections/simulate/skills/SkillFilesPanel";
import { refLabel } from "src/sections/simulate/skills/skillLabels";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import useAgentRoute from "./useAgentRoute";

const openSkill = (skillId) =>
  `${paths.dashboard.simulate.skills}?skill=${skillId}`;

const kindLabel = (kind) => (kind === "context" ? "Agent context" : "Skill");

const SCAN_STATUS_TEXT = {
  no_version: "This agent has no version yet.",
  not_scanned: "This version's code hasn't been read for skills.",
  unsupported_source:
    "Reading skills from a GitHub repository isn't available yet. Uploaded agents show them here.",
  source_missing: "The uploaded folder for this version is no longer stored.",
  too_large: "The uploaded folder is too large to read for skills.",
};

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

function LibrarySkills({ agentId }) {
  const navigate = useNavigate();
  const { data, isLoading, isError, refetch } = useAttachedSkills(agentId);

  if (isLoading) return <ContentSkeleton variant="rows" rows={3} />;
  if (isError)
    return (
      <ErrorState title="Couldn't load this agent's skills" onRetry={refetch} />
    );
  if (!data?.length)
    return (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="No library skills attached"
        description="Skills in the agent's own code still load as before. Attach a library skill to manage its versions here."
        action={
          <Button
            variant="contained"
            onClick={() => navigate(paths.dashboard.simulate.skills)}
          >
            Open skills
          </Button>
        }
      />
    );

  return (
    <Table size="small">
      <TableHead>
        <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
          <TableCell>Skill</TableCell>
          <TableCell>Kind</TableCell>
          <TableCell>Attached as</TableCell>
          <TableCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {data.map((skill) => (
          <TableRow
            key={skill.skill_id}
            hover
            sx={{ height: ROW_HEIGHT.default }}
          >
            <TableCell>
              <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                {skill.name}
              </Typography>
            </TableCell>
            <TableCell sx={{ color: "text.secondary" }}>
              {kindLabel(skill.kind)}
            </TableCell>
            <TableCell sx={{ color: "text.secondary" }}>
              {refLabel(skill)}
            </TableCell>
            <TableCell align="right">
              <Button
                size="small"
                onClick={() => navigate(openSkill(skill.skill_id))}
              >
                Open
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

LibrarySkills.propTypes = {
  agentId: PropTypes.string.isRequired,
};

function CodeSkillFilesDialog({ agentId, item, onClose }) {
  const { data, isLoading, isError, refetch } = useCodeSkillFiles(
    agentId,
    item?.id,
  );
  return (
    <ClosableDialog
      open={Boolean(item)}
      onClose={onClose}
      fullWidth
      maxWidth="md"
    >
      <DialogTitle sx={{ typography: "m1" }}>{item?.name}</DialogTitle>
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

CodeSkillFilesDialog.propTypes = {
  agentId: PropTypes.string.isRequired,
  item: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    path: PropTypes.string.isRequired,
  }),
  onClose: PropTypes.func.isRequired,
};

function CodeSkillMenu({ item, onOpen, onCopy, copying }) {
  const [anchor, setAnchor] = useState(null);
  const close = () => setAnchor(null);
  return (
    <>
      <IconButton
        size="small"
        aria-label={`Actions for ${item.name}`}
        onClick={(event) => setAnchor(event.currentTarget)}
      >
        <Iconify icon="solar:menu-dots-bold" width={16} />
      </IconButton>
      <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={close}>
        <MenuItem
          onClick={() => {
            close();
            onOpen(item);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:file-text-linear" width={16} />
          </ListItemIcon>
          Open files
        </MenuItem>
        <MenuItem
          disabled={!item.readable || copying}
          onClick={() => {
            close();
            onCopy(item);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          Copy to library
        </MenuItem>
      </Menu>
    </>
  );
}

CodeSkillMenu.propTypes = {
  item: PropTypes.shape({
    name: PropTypes.string.isRequired,
    readable: PropTypes.bool.isRequired,
  }).isRequired,
  onOpen: PropTypes.func.isRequired,
  onCopy: PropTypes.func.isRequired,
  copying: PropTypes.bool.isRequired,
};

function CodeSkills({ agentId }) {
  const { data, isLoading, isError, refetch } = useCodeSkills(agentId);
  const copy = useCopyCodeSkill(agentId);
  const [opened, setOpened] = useState(null);

  const onCopy = async (item) => {
    try {
      const skill = await copy.mutateAsync(item.id);
      enqueueSnackbar(
        `Copied ${skill?.name || item.name} to the library as v1`,
        {
          variant: "success",
        },
      );
    } catch (error) {
      enqueueSnackbar(error?.message || "Could not copy this skill", {
        variant: "error",
      });
    }
  };

  if (isLoading) return <ContentSkeleton variant="rows" rows={3} />;
  if (isError)
    return (
      <ErrorState
        title="Couldn't read the skills in this agent's code"
        onRetry={refetch}
      />
    );
  if (data?.status !== "scanned")
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {SCAN_STATUS_TEXT[data?.status] || SCAN_STATUS_TEXT.not_scanned}
      </Typography>
    );
  if (!data.items.length)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {`No skills or agent instructions in version ${data.agent_version_number}'s code.`}
      </Typography>
    );

  return (
    <>
      <Table size="small">
        <TableHead>
          <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
            <TableCell>Name</TableCell>
            <TableCell>Path</TableCell>
            <TableCell>Kind</TableCell>
            <TableCell>Status</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.items.map((item) => (
            <TableRow key={item.id} hover sx={{ height: ROW_HEIGHT.default }}>
              <TableCell>
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  {item.name}
                </Typography>
              </TableCell>
              <TableCell
                sx={{ color: "text.secondary", fontFamily: "monospace" }}
              >
                {item.path}
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                {kindLabel(item.kind)}
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                {item.readable ? "Readable" : problemLabel(item.problem)}
              </TableCell>
              <TableCell align="right">
                <CodeSkillMenu
                  item={item}
                  onOpen={setOpened}
                  onCopy={onCopy}
                  copying={copy.isPending}
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <CodeSkillFilesDialog
        agentId={agentId}
        item={opened}
        onClose={() => setOpened(null)}
      />
    </>
  );
}

CodeSkills.propTypes = {
  agentId: PropTypes.string.isRequired,
};

export default function AgentSkillsTab() {
  const { agentId } = useAgentRoute();
  const { error } = useAttachedSkills(agentId);

  if (isSkillsDisabled(error))
    return (
      <EmptyState
        icon="solar:lightbulb-bolt-linear"
        title="The skills library isn't turned on"
        description="Ask an admin to enable it for this workspace."
      />
    );

  return (
    <Stack spacing={3}>
      <Box>
        <SectionTitle
          title="Attached from the library"
          description="Library skills this agent loads, by tag or pinned version."
        />
        <LibrarySkills agentId={agentId} />
      </Box>
      <Box>
        <SectionTitle
          title="In this agent's code"
          description="Found in the latest version's uploaded folder. Read-only here; change them by uploading a new version or copy one to the library."
        />
        <CodeSkills agentId={agentId} />
      </Box>
    </Stack>
  );
}
