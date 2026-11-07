import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Box, Button } from "@mui/material";

import { useSkill, useSkillDraft } from "src/api/skills";
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import SkillEditorSession from "./SkillEditorSession";
import VersionDraftLoader from "./VersionDraftLoader";

function DraftInitializer({ skill }) {
  const query = useSkillDraft(skill.id);
  const [source, setSource] = useState(null);
  // Latch the starting snapshot. Refetches must not replace unsaved local edits.
  useEffect(() => {
    if (source || query.data === undefined || query.isError) return;
    if (query.data) setSource({ draft: query.data });
    else if (skill.latestVersion) setSource({ number: skill.latestVersion });
    else setSource({ draft: { files: [{ path: "SKILL.md", content: "" }], note: "", baseVersion: null, revision: 0 } });
  }, [source, query.data, query.isError, skill.latestVersion]);

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
  const { skillId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const preview = usePreviewState();
  const query = useSkill(skillId);
  const retry = () => {
    if (preview) { const next = new URLSearchParams(params); next.delete("state"); setParams(next, { replace: true }); }
    else query.refetch();
  };
  let content;
  if (preview === "loading" || (!preview && query.isPending)) content = <ContentSkeleton variant="page" label="Loading skill editor" />;
  else if (preview === "error" || (!preview && query.isError && !query.data)) content = <ErrorState title="Couldn't load this skill" onRetry={retry} />;
  else if (preview === "empty" || !query.data) content = <EmptyState title="No skill to edit" description="Open a skill from your library or create one to start a draft." action={<Button variant="contained" onClick={() => navigate(paths.dashboard.skills.root)}>Back to Skills</Button>} />;
  else content = <DraftInitializer key={skillId} skill={query.data} />;
  return <Box sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>{content}</Box>;
}
