import PropTypes from "prop-types";
import { useState } from "react";
import {
  Link as RouterLink,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import {
  Alert,
  Box,
  Button,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Tab,
  Typography,
} from "@mui/material";

import {
  downloadVersionZip,
  getSkillErrorCode,
  useArchiveSkill,
  useSkill,
} from "src/api/skills";
import { HOST_API } from "src/config-global";
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { ConfirmDialog } from "src/components/custom-dialog";
import { CustomTabs } from "src/components/tabs/tabs";
import LabelChip from "../components/LabelChip";
import { usePreviewState } from "../components/SkillsStatePreview";
import { buildInstallCommand } from "../utils/install";
import VersionsTab from "../versions/VersionsTab";
import FilesTab from "./FilesTab";
import UsageTab from "./UsageTab";
import UseSkillPanel from "./UseSkillPanel";
import { resolveViewedVersion } from "./detail-state";

const TABS = ["files", "versions", "usage", "use"];
const TAB_LABELS = {
  files: "Files",
  versions: "Versions",
  usage: "Usage",
  use: "Use",
};

function DetailContent({ skill, preview }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const archive = useArchiveSkill();
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const tab = TABS.includes(params.get("tab")) ? params.get("tab") : "files";
  const production = skill.labels.find((label) => label.name === "production");
  const otherDrafts = skill.drafts.filter((draft) => !draft.isMine);
  const { number: downloadNumber } = resolveViewedVersion(
    skill,
    params.get("version"),
  );
  const changeTab = (value) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("tab", value);
      return next;
    });
  const copyCommand = async () => {
    setMenuAnchor(null);
    if (!production) return;
    try {
      await navigator.clipboard.writeText(
        buildInstallCommand({
          hostApi: HOST_API,
          names: [skill.name],
          label: "production",
          dir: ".claude/skills",
        }),
      );
      enqueueSnackbar("Install command copied", { variant: "success" });
    } catch {
      enqueueSnackbar("Could not copy. Open Use skill to view the command.", {
        variant: "error",
      });
    }
  };
  const download = async () => {
    setMenuAnchor(null);
    setDownloading(true);
    try {
      await downloadVersionZip(skill.id, downloadNumber);
    } catch {
      enqueueSnackbar("Could not download this version. Try again.", {
        variant: "error",
      });
    } finally {
      setDownloading(false);
    }
  };
  const confirmArchive = async () => {
    try {
      await archive.mutateAsync(skill.id);
      enqueueSnackbar("Skill archived", { variant: "success" });
      navigate(paths.dashboard.skills.root);
    } catch {
      enqueueSnackbar("Could not archive this skill. Try again.", {
        variant: "error",
      });
    }
  };

  return (
    <>
      <PageHeader
        title={
          <Box component="span" sx={{ fontFamily: "monospace" }}>
            {skill.name}
          </Box>
        }
        description={skill.description}
        badge={
          production && (
            <LabelChip name="production" version={production.version} />
          )
        }
        back={
          <IconButton
            component={RouterLink}
            to={paths.dashboard.skills.root}
            aria-label="Back to Skills"
          >
            <Iconify icon="solar:arrow-left-linear" />
          </IconButton>
        }
        sx={{ flexWrap: "wrap", gap: 1.5 }}
        actions={
          <>
            <Button variant="outlined" onClick={() => changeTab("use")}>
              Use skill
            </Button>
            <Button
              variant="contained"
              component={RouterLink}
              to={paths.dashboard.skills.edit(skill.id)}
            >
              {skill.hasDraft ? "Continue draft" : "Edit"}
            </Button>
            <IconButton
              aria-label="Skill actions"
              aria-haspopup="menu"
              aria-expanded={Boolean(menuAnchor)}
              aria-controls={menuAnchor ? "skill-actions" : undefined}
              onClick={(event) => setMenuAnchor(event.currentTarget)}
            >
              <Iconify icon="solar:menu-dots-bold" />
            </IconButton>
          </>
        }
      />
      {otherDrafts.length > 0 && (
        <Alert severity="info" sx={{ mb: 1.5 }}>
          {otherDrafts.map((draft) => draft.user.name).join(", ")}{" "}
          {otherDrafts.length === 1 ? "has" : "have"} a draft in progress. Their
          drafts do not change published versions.
        </Alert>
      )}
      <CustomTabs
        value={tab}
        onChange={(_event, value) => changeTab(value)}
        variant="scrollable"
        scrollButtons="auto"
        aria-label="Skill detail"
      >
        {TABS.map((value) => (
          <Tab
            key={value}
            value={value}
            label={TAB_LABELS[value]}
            id={`skill-tab-${value}`}
            aria-controls={`skill-panel-${value}`}
          />
        ))}
      </CustomTabs>
      <Box
        role="tabpanel"
        id={`skill-panel-${tab}`}
        aria-labelledby={`skill-tab-${tab}`}
        sx={{ pt: 3, minWidth: 0 }}
      >
        {tab === "files" &&
          (preview === "empty" ? (
            <EmptyState
              title="Not published yet"
              description="Your files are in a draft. Publish a version to browse it here."
            />
          ) : (
            <FilesTab skill={skill} />
          ))}
        {tab === "versions" && <VersionsTab skill={skill} />}
        {tab === "usage" && <UsageTab skill={skill} />}
        {tab === "use" && <UseSkillPanel skill={skill} />}
      </Box>
      <Menu
        id="skill-actions"
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => setMenuAnchor(null)}
      >
        <MenuItem disabled={!downloadNumber || downloading} onClick={download}>
          <ListItemIcon>
            <Iconify icon="solar:download-linear" width={16} />
          </ListItemIcon>
          Download ZIP
        </MenuItem>
        <MenuItem
          disabled={!production}
          onClick={copyCommand}
          aria-describedby={!production ? "install-command-guidance" : undefined}
        >
          <ListItemIcon>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          Copy install command
        </MenuItem>
        {!production && (
          <Typography
            id="install-command-guidance"
            component="li"
            role="note"
            variant="s3"
            color="text.secondary"
            sx={{ px: 2, py: 1, maxWidth: 280, listStyle: "none" }}
          >
            Assign production, or open Use skill to choose an existing label or
            pinned version.
          </Typography>
        )}
        <MenuItem
          onClick={() => {
            setMenuAnchor(null);
            setConfirmOpen(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:archive-linear" width={16} />
          </ListItemIcon>
          Archive
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={confirmOpen}
        onClose={() => {
          if (!archive.isPending) setConfirmOpen(false);
        }}
        closeDisabled={archive.isPending}
        title="Archive skill?"
        content={`Archive ${skill.name}? It will be hidden from the library and agents will no longer be able to fetch it. Published versions and label history are retained.`}
        action={
          <Button
            variant="contained"
            color="error"
            disabled={archive.isPending}
            onClick={confirmArchive}
          >
            {archive.isPending ? "Archiving…" : "Archive"}
          </Button>
        }
      />
    </>
  );
}
DetailContent.propTypes = {
  skill: PropTypes.object.isRequired,
  preview: PropTypes.string,
};

export default function SkillDetailView() {
  const { skillId } = useParams();
  const [params, setParams] = useSearchParams();
  const preview = usePreviewState();
  const query = useSkill(skillId);
  const retry = () => {
    if (preview) {
      const next = new URLSearchParams(params);
      next.delete("state");
      setParams(next, { replace: true });
    }
    query.refetch();
  };
  const notFound = getSkillErrorCode(query.error) === "skill_not_found";
  return (
    <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
      {preview === "loading" || query.isPending ? (
        <ContentSkeleton variant="page" label="Loading skill" />
      ) : preview === "error" || query.isError ? (
        notFound && preview !== "error" ? (
          <EmptyState
            title="Skill not found"
            description="It may have been archived, or it may belong to another workspace."
            action={
              <Button
                component={RouterLink}
                to={paths.dashboard.skills.root}
                variant="contained"
              >
                Back to Skills
              </Button>
            }
          />
        ) : (
          <ErrorState
            title="Could not load this skill"
            detail="Check your connection and try again."
            onRetry={retry}
          />
        )
      ) : query.data ? (
        <DetailContent
          key={query.data.id}
          skill={query.data}
          preview={preview}
        />
      ) : (
        <EmptyState
          title="Skill not found"
          description="Return to the library to choose a skill."
        />
      )}
    </Stack>
  );
}
