import PropTypes from "prop-types";
import { useState } from "react";
import {
  Link as RouterLink,
  useLocation,
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
  Skeleton,
  Stack,
  Tab,
  Typography,
} from "@mui/material";

import {
  downloadVersionZip,
  getSkillErrorCode,
  useArchiveSkill,
  useSkill,
  useSkillVersion,
} from "src/api/skills";
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextDependents } from "src/api/skills/context-dependents";
import { HOST_API } from "src/config-global";
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import ActionReason from "../components/ActionReason";
import { useSkillsAccess } from "../utils/access";
import ArchiveConfirmContent from "../library/archiveCopy";
import { useContextAuthoring } from "../contexts/ContextLibraryView";
import ObjectHeader from "../components/ObjectHeader";
import VersionSelect from "../components/VersionSelect";
import { ClosableDialog } from "src/components/custom-dialog";
import VersionIdentityRow from "../components/VersionIdentityRow";
import OperationFeedback from "../components/OperationFeedback";
import { ConfirmDialog } from "src/components/custom-dialog";
import { CustomTabs } from "src/components/tabs/tabs";
import { usePreviewState } from "../components/SkillsStatePreview";
import { buildInstallCommand } from "../utils/install";
import TestsTab from '../tests/TestsTab';
import ContextDependentsPanel from '../contexts/ContextDependentsPanel';
import GitHubSourcePanel, { GitHubSourceStatus } from '../sources/GitHubSourcePanel';
import VersionsTab from "../versions/VersionsTab";
import FilesTab from "./FilesTab";
import J1MigrationExport from './J1MigrationExport';
import UsageTab from "./UsageTab";
import UseSkillPanel from "./UseSkillPanel";
import { resolveViewedVersion } from "./detail-state";

const TABS = ["files", "tests", "dependents", "versions", "usage", "use"];
const TAB_LABELS = {
  files: "Files",
  tests: "Tests",
  dependents: "Used by",
  versions: "Versions",
  usage: "Activity",
  use: "Install",
};

// Mirrors ContextDependentsPanel: one reference per context (production first, else the newest), eligible when it follows a label with an update.
function hasEligibleDependent(rows = []) {
  const ids = [...new Set(rows.map((row) => row.context_id))];
  return ids.some((id) => {
    const references = rows.filter((row) => row.context_id === id);
    const chosen = references.find((row) => row.labels?.includes("production")) || references.reduce((latest, row) => (row.context_version > latest.context_version ? row : latest));
    return !chosen.requested?.version && chosen.update_state === "update_available";
  });
}

function DetailContent({ skill, preview }) {
  const navigate = useNavigate();
  const { isViewer, canWrite } = useSkillsAccess();
  const [sourceOpen, setSourceOpen] = useState(false);
  const [params, setParams] = useSearchParams();
  const archive = useArchiveSkill();
  const canRestore = Boolean(useContextAuthoring()?.bindings?.archived);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [archiveRevision, setArchiveRevision] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [operationError, setOperationError] = useState(null);
  const tab = TABS.includes(params.get("tab")) ? params.get("tab") : "files";
  const production = skill.labels.find((label) => label.name === "production");
  const otherDrafts = (skill.drafts || []).filter((draft) => !draft.isMine);
  const { number: downloadNumber } = resolveViewedVersion(
    skill,
    params.get("version"),
  );
  const viewedVersion = useSkillVersion(skill.id, downloadNumber);
  // The Used by panel draws its own contained "Prepare draft updates" when one context can follow an update.
  // Read the same cached query (and the same one-row-per-context reduction) so the header and panel never both lead.
  const dependents = useContextDependents(tab === "dependents" ? skill.id : undefined, { view: "active", page: 1 });
  const panelHasAction = hasEligibleDependent(dependents.data?.results);
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
    setOperationError(null);
    try {
      await downloadVersionZip(skill.id, downloadNumber);
    } catch (error) {
      setOperationError({ error: { ...error, code: "retryable_transport", message: error.message }, action: "download this version" });
    } finally {
      setDownloading(false);
    }
  };
  const confirmArchive = async () => {
    setOperationError(null);
    try {
      await archive.mutateAsync({ id: skill.id, expectedReleaseRevision: archiveRevision });
      enqueueSnackbar("Skill archived", { variant: "success" });
      navigate(paths.dashboard.skills.root);
    } catch (error) {
      setConfirmOpen(false);
      setOperationError({ error, action: "archive this skill" });
    }
  };

  return (
    <>
      <ObjectHeader
        crumbs={[{ label: "Skills", to: paths.dashboard.skills.root }, { label: skill.name }]}
        title={skill.name}
        purpose={skill.description}
        identity={<VersionIdentityRow picker={<Box sx={{ minWidth: 140 }}><VersionSelect ariaLabel="Viewing" includeLabels={false} annotate skillId={skill.id} value={downloadNumber} onChange={(value) => setParams((previous) => { const next = new URLSearchParams(previous); next.set('version', String(value)); next.set('path', 'SKILL.md'); return next; })} /></Box>} viewing={{ number: downloadNumber, digest: viewedVersion.data?.digest }} latest={{ number: skill.latestVersion }} production={production ? { number: production.version } : null} draft={skill.hasDraft ? { baseNumber: skill.drafts?.find((draft) => draft.isMine)?.baseVersion } : null} />}
        sx={{ flexWrap: "wrap", gap: 1.5 }}
        actions={
          <>

            {isViewer && <Button variant="contained" color="primary" onClick={() => changeTab("use")}>
              Use skill
            </Button>}
            <Button
              aria-describedby={isViewer ? "detail-action-reason-1" : undefined}
              disabled={!canWrite}
              variant={isViewer || tab === 'tests' || (tab === 'dependents' && panelHasAction) ? 'outlined' : 'contained'}
              color="primary"
              component={!canWrite ? "button" : RouterLink}
              to={!canWrite ? undefined : paths.dashboard.skills.edit(skill.id)}
            >
              {skill.hasDraft ? 'Resume your draft' : skill.latestVersion ? `Edit latest v${skill.latestVersion}` : 'Edit unpublished draft'}
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
      {isViewer && <ActionReason id="detail-action-reason-1" reason="Viewers can't edit. Ask a workspace admin for edit access." />}
      {!skill.hasDraft && downloadNumber && downloadNumber !== skill.latestVersion && <Typography variant="s3" color="text.secondary" sx={{ mb: 1.5 }}>Editing starts from latest v{skill.latestVersion}, not the v{downloadNumber} you&apos;re viewing.</Typography>}
      {operationError && <OperationFeedback {...operationError} onRetry={operationError.action.startsWith('download') ? download : undefined} onRefresh={() => window.location.reload()} onReconcile={() => window.location.reload()} />}
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
            // A linked skill keeps its GitHub update check beside its files.
            <Stack spacing={3}><FilesTab skill={skill} /><GitHubSourcePanel skill={skill} linkedOnly /></Stack>
          ))}
        {tab === "tests" && <TestsTab skill={skill} />}
        {tab === "dependents" && <ContextDependentsPanel skillId={skill.id} state={preview} />}
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
        <MenuItem disabled={!downloadNumber} onClick={() => { setMenuAnchor(null); setExportOpen(true); }}><ListItemIcon><Iconify icon="solar:download-linear" width={16} /></ListItemIcon>Export with source paths</MenuItem>
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
          aria-describedby={skill.permissions?.canArchive !== true ? "detail-action-reason-2" : undefined}
          disabled={skill.permissions?.canArchive !== true}
          onClick={() => {
            setMenuAnchor(null);
            setArchiveRevision(skill.releaseRevision);
            setConfirmOpen(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:archive-linear" width={16} />
          </ListItemIcon>
          Archive
        </MenuItem>
        {skill.permissions?.canArchive !== true && <ActionReason id="detail-action-reason-2" reason="You can't archive this skill. Ask a workspace admin." />}
        <GitHubSourceStatus skill={skill} onOpen={() => { setMenuAnchor(null); setSourceOpen(true); }} />
      </Menu>
      <ClosableDialog open={sourceOpen} onClose={() => setSourceOpen(false)} aria-label="GitHub source"><Box sx={{ p: 2, pr: 6 }}><GitHubSourcePanel skill={skill} /></Box></ClosableDialog>
      <ClosableDialog open={exportOpen} onClose={() => setExportOpen(false)} aria-label="Export with source paths"><Box sx={{ p: 2, pr: 6 }}><J1MigrationExport resourceId={skill.id} number={downloadNumber} /></Box></ClosableDialog>
      <ConfirmDialog
        open={confirmOpen}
        onClose={() => {
          if (!archive.isPending) setConfirmOpen(false);
        }}
        closeDisabled={archive.isPending}
        title={`Archive ${skill.name}?`}
        content={<ArchiveConfirmContent labels={skill.labels} canRestore={canRestore} />}
        action={
          <Button
            variant="contained"
            color="error"
            disabled={archive.isPending || skill.permissions?.canArchive !== true}
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

// The same header the loaded page has, so nothing jumps when the skill arrives (principle 7):
// breadcrumb, title, one line of purpose, the identity chips and the tab strip.
function SkillHeaderSkeleton() {
  return (
    <Box role="progressbar" aria-live="polite" aria-busy="true" aria-label="Loading skill" sx={{ width: 1, minWidth: 0, "& .MuiSkeleton-root": { bgcolor: "divider" } }}>
      <Stack spacing={1.5} sx={{ minWidth: 0 }}>
        <Skeleton variant="rounded" width={140} height={12} />
        <Skeleton variant="rounded" width={200} height={20} />
        <Skeleton variant="rounded" width="min(420px, 100%)" height={12} />
        <Stack direction="row" spacing={1} alignItems="center">
          <Skeleton variant="rounded" width={150} height={28} />
          <Skeleton variant="rounded" width={96} height={20} />
          <Skeleton variant="rounded" width={72} height={12} />
        </Stack>
        <Stack direction="row" spacing={3} sx={{ borderBottom: 1, borderColor: "divider", pb: 1.25 }}>
          {[36, 40, 72, 56, 52, 28].map((width, index) => <Skeleton key={width + index} variant="rounded" width={width} height={12} />)}
        </Stack>
      </Stack>
      <Box sx={{ pt: 3 }}><ContentSkeleton variant="panel" role="presentation" label="Loading skill details" sx={{ "& > .MuiStack-root": { p: 0 } }} /></Box>
    </Box>
  );
}

export default function SkillDetailView() {
  const { skillId } = useParams();
  const location = useLocation();
  // The library hands over the name so the breadcrumb and title survive a failed load.
  const knownName = typeof location.state?.skillName === "string" ? location.state.skillName : "";
  const missingHeader = <ObjectHeader crumbs={[{ label: "Skills", to: paths.dashboard.skills.root }, { label: knownName || "Skill" }]} title={knownName || "Skill"} />;
  const { identity } = useSkillV11Scope();
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
        <SkillHeaderSkeleton />
      ) : preview === "error" || query.isError ? (
        notFound && preview !== "error" ? (
          <Stack spacing={3} sx={{ minWidth: 0 }}>{missingHeader}<EmptyState
            title="Skill not found"
            description="It may have been archived, or it may belong to another workspace."
            action={
              <Button
                component={RouterLink}
                to={paths.dashboard.skills.root}
                variant="outlined"
              >
                Back to Skills
              </Button>
            }
          /></Stack>
        ) : (
          <Stack spacing={3} sx={{ minWidth: 0 }}>{missingHeader}<ErrorState
            title="Could not load this skill"
            detail="Check your connection and try again."
            onRetry={retry}
          /></Stack>
        )
      ) : query.data ? (
        <DetailContent
          key={`${identity}:${query.data.id}`}
          skill={query.data}
          preview={preview}
        />
      ) : (
        <Stack spacing={3} sx={{ minWidth: 0 }}>{missingHeader}<EmptyState
          title="Skill not found"
          description="It may have been archived, or it may belong to another workspace."
          action={<Button component={RouterLink} to={paths.dashboard.skills.root} variant="outlined">Back to Skills</Button>}
        /></Stack>
      )}
    </Stack>
  );
}
