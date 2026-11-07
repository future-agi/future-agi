import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";

import { useSkillCompare } from "src/api/skills";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";
import TextDiff from "src/sections/skills/components/TextDiff";

function ChangesList({ files, baseVersion }) {
  const [selected, setSelected] = useState(null);
  const sorted = [...files].sort((a, b) => (
    Number(a.status === "unchanged") - Number(b.status === "unchanged") || a.path.localeCompare(b.path)
  ));
  return (
    <Stack spacing={1}>
      <Typography variant="m3" fontWeight={500}>Changes {baseVersion ? `since v${baseVersion}` : "in this first version"}</Typography>
      {sorted.map((file) => (
        <Box key={file.path}>
          <Button
            fullWidth
            variant="text"
            color="inherit"
            aria-expanded={selected === file.path}
            disabled={file.status === "unchanged"}
            onClick={() => setSelected(selected === file.path ? null : file.path)}
            sx={{ justifyContent: "space-between", gap: 1, textAlign: "left" }}
          >
            <Typography variant="s1" sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{file.path}</Typography>
            <FileStatusBadge status={file.status} />
          </Button>
          {selected === file.path && (
            <TextDiff fromText={file.fromText ?? ""} toText={file.toText ?? ""} fromTitle={baseVersion ? `v${baseVersion}` : "No previous version"} toTitle="Your draft" />
          )}
        </Box>
      ))}
    </Stack>
  );
}

ChangesList.propTypes = { files: PropTypes.array.isRequired, baseVersion: PropTypes.number };

function SavedChanges({ skillId, baseVersion }) {
  const query = useSkillCompare(skillId, baseVersion, "draft");
  if (query.isError) return <ErrorState title="Couldn't load file changes" onRetry={query.refetch} />;
  if (!query.data) return <ContentSkeleton variant="rows" label="Loading file changes" />;
  return <ChangesList files={query.data.files} baseVersion={baseVersion} />;
}

SavedChanges.propTypes = { skillId: PropTypes.string.isRequired, baseVersion: PropTypes.number.isRequired };

export default function PublishChanges({ skillId, draft }) {
  if (draft.baseVersion !== null) return <SavedChanges skillId={skillId} baseVersion={draft.baseVersion} />;
  return <ChangesList files={draft.files.map((file) => ({ path: file.path, status: "added", fromText: "", toText: file.content }))} />;
}

PublishChanges.propTypes = {
  skillId: PropTypes.string.isRequired,
  draft: PropTypes.shape({ baseVersion: PropTypes.number, files: PropTypes.array.isRequired }).isRequired,
};
