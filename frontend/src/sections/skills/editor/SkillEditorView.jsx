import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Box, Button } from "@mui/material";

import { useSkill, useSkillDraft } from "src/api/skills";
import { useSkillV11Scope } from 'src/api/skills/testing';
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import { useSkillsAccess } from "../utils/access";
import ObjectHeader from "../components/ObjectHeader";
import CapabilityNotice from "../components/CapabilityNotice";
import SkillEditorSession from "./SkillEditorSession";
import VersionDraftLoader from "./VersionDraftLoader";

function DraftInitializer({ skill }) {
  const query = useSkillDraft(skill.id);
  const [params] = useSearchParams();
  const preservingMigration = params.get('preserve') === 'migration';
  const [source, setSource] = useState(null);
  // Revalidate stale cache before starting; later refetches must not replace local edits.
  useEffect(() => {
    if (source || query.data === undefined || query.isError || query.isFetching || query.isPaused) return;
    if (query.data) setSource({ draft: query.data });
    else if (preservingMigration) setSource({ missing: true });
    else if (skill.latestVersion) setSource({ number: skill.latestVersion });
    else setSource({ draft: { files: [{ path: "SKILL.md", content: "" }], note: "", baseVersion: null, revision: 0 } });
  }, [source, query.data, query.isError, query.isFetching, query.isPaused, skill.latestVersion, preservingMigration]);

  if (source?.missing) return <EmptyState title="No saved draft to preserve" description="Preview the retained source again. Recovery never creates a replacement draft." />;
  if (source?.draft) return <SkillEditorSession skill={skill} initialDraft={source.draft} refetchDraft={query.refetch} />;
  if (source?.number) return (
    <VersionDraftLoader skillId={skill.id} number={source.number}>
      {(draft) => <SkillEditorSession skill={skill} initialDraft={draft} refetchDraft={query.refetch} />}
    </VersionDraftLoader>
  );
  if (query.isError) return <ErrorState title="Couldn't load your draft" onRetry={query.refetch} />;
  return <ContentSkeleton variant="page" label="Loading your draft" />;
}

DraftInitializer.propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, latestVersion: PropTypes.number }).isRequired,
};

export default function SkillEditorView() {
  const { ready, canWrite } = useSkillsAccess();
  useEffect(() => { if (canWrite) import("@monaco-editor/react").then(({ loader }) => loader.init()).catch(() => {}); }, [canWrite]);
  const { skillId } = useParams();
  const { identity } = useSkillV11Scope();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const preview = usePreviewState();
  const query = useSkill(skillId);
  const retry = () => {
    if (preview) { const next = new URLSearchParams(params); next.delete("state"); setParams(next, { replace: true }); }
    else query.refetch();
  };
  let content;
  if (!ready || preview === "loading" || (!preview && query.isPending)) content = <ContentSkeleton variant="page" label="Loading skill editor" />;
  else if (preview === "error" || (!preview && query.isError && !query.data)) content = <ErrorState title="Couldn't load this skill" onRetry={retry} />;
  else if (preview === "empty" || !query.data) content = <EmptyState title="No skill to edit" description="Open a skill from your library or create one to start a draft." action={<Button variant="contained" color="primary" onClick={() => navigate(paths.dashboard.skills.root)}>Back to Skills</Button>} />;
  else if (!canWrite) content = <>
    <ObjectHeader title={query.data.name} crumbs={[{ label: "Skills", to: paths.dashboard.skills.root }, { label: query.data.name }]} />
    <CapabilityNotice title="You can view this skill but not edit it" description="Ask a workspace admin for edit access." actions={<>
      <Button component={RouterLink} to={paths.dashboard.skills.detail(skillId)} variant="outlined">Back to {query.data.name}</Button>
      <Button component={RouterLink} to={`${paths.dashboard.skills.detail(skillId)}?tab=use`} variant="outlined">Use skill</Button>
    </>} />
  </>;
  else content = <DraftInitializer key={`${identity}:${skillId}`} skill={query.data} />;
  return <Box sx={{ p: { xs: 2, md: 3 }, minWidth: 0, minHeight: 0, flex: 1, display: "flex", flexDirection: "column", overflowY: { xs: "auto", md: "hidden" } }}>{content}</Box>;
}
