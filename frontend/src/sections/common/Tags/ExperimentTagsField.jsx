// W5-3 (C-H1): the Tags field of `Edit experiment`. Chips of the draft and
// `Add tags` / `Edit tags`, which opens the one TagPicker (create and
// Manage tags… included). The dialog's Save writes the draft
// (useExperimentTagsDraft.save).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import TagChips from "./TagChips";
import TagPicker from "./TagPicker";
import { tagsByIds } from "./tagModel";

export default function ExperimentTagsField({ draft }) {
  const [anchor, setAnchor] = useState(null);
  const chosen = tagsByIds(draft.tags, draft.ids);
  return (
    <Stack gap={0.75} data-testid="experiment-tags-field">
      <Typography typography="s2" color="text.secondary">
        Tags
      </Typography>
      <Stack direction="row" flexWrap="wrap" alignItems="center" gap={0.75}>
        <TagChips tags={chosen} />
        <Button
          size="small"
          variant="text"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mdi:tag-outline" width={16} />}
          data-testid="experiment-tags-edit"
        >
          {chosen.length ? "Edit tags" : "Add tags"}
        </Button>
      </Stack>
      {draft.error ? (
        <Typography typography="s3" color="error.main" role="alert">
          {draft.error}
        </Typography>
      ) : null}
      <TagPicker
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        itemTagIds={[draft.ids]}
        onChange={(next) => draft.setIds(next[0] ?? [])}
        title="Tags on this experiment"
      />
    </Stack>
  );
}

ExperimentTagsField.propTypes = {
  draft: PropTypes.shape({
    ids: PropTypes.array.isRequired,
    setIds: PropTypes.func.isRequired,
    tags: PropTypes.array.isRequired,
    error: PropTypes.string,
  }).isRequired,
};
