import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Alert, Box, Button, IconButton, Stack, Tab, Typography } from "@mui/material";

import { useDiscardSkillDraft } from "src/api/skills";
import { paths } from "src/routes/paths";
import PageHeader from "src/components/page-header";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { SegmentedTabs } from "src/components/tabs/tabs";
import FileTree from "src/sections/skills/components/FileTree";
import SkillMarkdownPreview from "src/sections/skills/components/SkillMarkdownPreview";
import DraftChecksPanel from "./DraftChecksPanel";
import DraftConflictDialog from "./DraftConflictDialog";
import PublishDialog from "./PublishDialog";
import SkillFileEditor from "./SkillFileEditor";
import useDraftSession from "./useDraftSession";
import useUnsavedNavigation from "./useUnsavedNavigation";

export default function SkillEditorSession({ skill, initialDraft, refetchDraft }) {
  const navigate = useNavigate();
  const [discardOpen, setDiscardOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [publishDraft, setPublishDraft] = useState(null);
  const [preparing, setPreparing] = useState(false);
  const discard = useDiscardSkillDraft(skill.id);
  const session = useDraftSession(skill.id, initialDraft, discardOpen || Boolean(publishDraft));
  const guard = useUnsavedNavigation(session.dirty || session.status === "saving");
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const [mode, setMode] = useState("edit");
  const [target, setTarget] = useState(null);
  const locked = preparing || discardOpen || Boolean(publishDraft) || discard.isPending;
  const saving = session.status === "saving";
  const file = session.files.find((item) => item.path === selectedPath) ?? session.files[0];
  const isMarkdown = /\.(md|markdown)$/i.test(file?.path ?? "");
  const stale = skill.latestVersion > (session.baseVersion ?? 0);

  useEffect(() => { if (session.status === "conflict") setConflictOpen(true); }, [session.status]);

  const openFile = (path, line) => {
    if (!session.files.some((item) => item.path === path)) return;
    setSelectedPath(path);
    setTarget(line ? { line } : null);
    if (line) setMode("edit");
  };
  const startPublish = async () => {
    setPreparing(true);
    const saved = await session.save(session.revision);
    if (saved) setPublishDraft(saved);
    setPreparing(false);
  };
  const compareLatest = async () => {
    setPreparing(true);
    const saved = await session.save(session.revision);
    if (saved) guard.allow(() => navigate(`${paths.dashboard.skills.compare(skill.id)}?from=draft&to=${skill.latestVersion}`));
    setPreparing(false);
  };
  const discardDraft = async () => {
    if (saving) return;
    try {
      await discard.mutateAsync();
      enqueueSnackbar("Draft discarded.", { variant: "success" });
      guard.allow(() => navigate(paths.dashboard.skills.detail(skill.id)));
    } catch {
      enqueueSnackbar("Couldn't discard the draft. Your edits are still here.", { variant: "error" });
    }
  };
  let saveLabel = "Saved";
  if (session.status === "conflict") saveLabel = "Conflict";
  else if (session.status === "error") saveLabel = "Couldn't save";
  else if (session.dirty || saving) saveLabel = "Saving…";
  else if (session.revision === 0) saveLabel = "Not saved yet";

  return (
    <Stack spacing={2}>
      <PageHeader
        title={<Box component="span" sx={{ fontFamily: "monospace" }}>{skill.name}</Box>}
        description={session.baseVersion === null ? "New skill" : `Draft based on v${session.baseVersion}`}
        back={<IconButton aria-label="Back to skill" onClick={() => navigate(paths.dashboard.skills.detail(skill.id))}><Iconify icon="solar:arrow-left-linear" /></IconButton>}
        actions={(
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", justifyContent: "flex-end", alignItems: "center" }}>
            <Typography role="status" variant="s3" color="text.secondary">{saveLabel}</Typography>
            {session.status === "error" && <Button size="small" onClick={() => session.save()}>Retry</Button>}
            {session.status === "conflict" && <Button size="small" onClick={() => setConflictOpen(true)}>Resolve conflict</Button>}
            <Button variant="outlined" disabled={locked || saving} onClick={() => setDiscardOpen(true)}>Discard draft</Button>
            <Button variant="contained" disabled={locked || saving || session.status === "conflict" || session.status === "error"} onClick={startPublish}>Publish</Button>
          </Box>
        )}
        sx={{ flexWrap: "wrap", rowGap: 1.5 }}
      />
      {stale && (
        <Alert severity="warning" action={<Button color="inherit" disabled={locked || saving || session.status === "conflict"} onClick={compareLatest}>Compare</Button>}>
          v{skill.latestVersion} was published after you started. Compare your saved draft with the latest version before publishing.
        </Alert>
      )}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", md: "200px minmax(0, 1fr)", lg: "200px minmax(0, 1fr) 280px" }, gap: 3, alignItems: "start" }}>
        <Box sx={{ minWidth: 0 }}>
          <FileTree
            files={session.files}
            selectedPath={file?.path}
            onSelect={openFile}
            readOnly={locked}
            onCreate={(path) => {
              if (locked || session.files.some((item) => item.path.toLowerCase() === path.toLowerCase())) return;
              session.edit([...session.files, { path, content: "" }]);
              setSelectedPath(path);
              setTarget(null);
              setMode("edit");
            }}
            onRename={(from, to) => {
              if (locked || from === "SKILL.md" || session.files.some((item) => item.path.toLowerCase() === to.toLowerCase())) return;
              session.edit(session.files.map((item) => item.path === from ? { ...item, path: to } : item));
              setSelectedPath(to);
            }}
            onDelete={(path) => {
              if (locked || path === "SKILL.md") return;
              session.edit(session.files.filter((item) => item.path !== path));
              if (file?.path === path) setSelectedPath("SKILL.md");
            }}
          />
        </Box>
        <Stack spacing={1.5} sx={{ minWidth: 0 }}>
          {isMarkdown && <SegmentedTabs value={mode} onChange={(_, value) => setMode(value)} aria-label="Markdown view"><Tab value="edit" label="Edit" /><Tab value="preview" label="Preview" /></SegmentedTabs>}
          {file && (isMarkdown && mode === "preview" ? (
            <SkillMarkdownPreview content={file.content} currentPath={file.path} onOpenPath={openFile} />
          ) : (
            <SkillFileEditor key={file.path} file={file} target={target} readOnly={locked} onChange={(content) => {
              if (!locked) session.edit(session.files.map((item) => item.path === file.path ? { ...item, content } : item));
            }} />
          ))}
        </Stack>
        <Box sx={{ minWidth: 0, gridColumn: { md: "1 / -1", lg: "auto" } }}>
          <DraftChecksPanel files={session.files} name={skill.name} onSelect={(check) => openFile(check.path, check.line)} />
        </Box>
      </Box>
      <DraftConflictDialog open={conflictOpen} onClose={() => setConflictOpen(false)} refetch={refetchDraft} onOverwrite={session.save} onReload={(draft) => {
        if (draft) session.reload(draft);
        else guard.allow(() => navigate(paths.dashboard.skills.detail(skill.id)));
      }} />
      <ConfirmDialog open={discardOpen} onClose={() => { if (!discard.isPending) setDiscardOpen(false); }} closeDisabled={discard.isPending} title="Discard draft?" content="This removes your saved draft and unsaved edits. Published versions and labels will not change." action={<Button variant="contained" color="error" disabled={saving || discard.isPending} onClick={discardDraft}>Discard draft</Button>} />
      <ConfirmDialog open={guard.blocked} onClose={guard.cancel} title="Leave with unsaved changes?" content="Your latest edits have not been saved. Stay to save them, or leave without those changes." action={<Button variant="contained" color="error" onClick={guard.proceed}>Leave without saving</Button>} />
      {publishDraft && <PublishDialog open onClose={() => setPublishDraft(null)} onDraftChange={session.reload} skill={skill} draft={publishDraft} />}
    </Stack>
  );
}

SkillEditorSession.propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired, latestVersion: PropTypes.number }).isRequired,
  initialDraft: PropTypes.shape({ files: PropTypes.array.isRequired, note: PropTypes.string.isRequired, revision: PropTypes.number.isRequired, baseVersion: PropTypes.number }).isRequired,
  refetchDraft: PropTypes.func.isRequired,
};
