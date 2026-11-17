// W5-3 (W5-SCOPE §7 C1): Manage tags (560 px). Name · Colour · Description ·
// Used on · Delete, with the confirmation `Removes “candidate” from 3
// experiments and 12 rows` (counts from the registry's live used_on).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  TAG_PALETTE,
  deleteConfirmText,
  tagColor,
  tagNameError,
  usedOnText,
} from "./tagModel";
import { tagErrorText, useDeleteTag, useTags, useUpdateTag } from "./useTags";

function TagRow({ tag, tags }) {
  const update = useUpdateTag();
  const del = useDeleteTag();
  const [name, setName] = useState(tag.name);
  const [description, setDescription] = useState(tag.description ?? "");
  const [palette, setPalette] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const save = async (patch) => {
    setError("");
    try {
      await update.mutateAsync({ id: tag.id, ...patch });
    } catch (e) {
      setError(String(tagErrorText(e)));
    }
  };
  const nameErr =
    name !== tag.name ? tagNameError(name, tags, { exceptId: tag.id }) : null;

  return (
    <Box
      data-testid="manage-tag-row"
      data-tag-id={tag.id}
      sx={{ py: 1, borderBottom: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" gap={1} alignItems="center">
        <Tooltip title="Colour">
          <IconButton
            size="small"
            aria-label={`Colour of ${tag.name}`}
            aria-expanded={palette}
            onClick={() => setPalette((v) => !v)}
          >
            <Box
              sx={{
                width: 14,
                height: 14,
                borderRadius: "50%",
                bgcolor: tagColor(tag.color),
              }}
            />
          </IconButton>
        </Tooltip>
        <TextField
          size="small"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name !== tag.name && !nameErr && save({ name })}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          error={Boolean(nameErr)}
          inputProps={{ "aria-label": `Name of ${tag.name}` }}
          sx={{ width: 150 }}
        />
        <TextField
          size="small"
          value={description}
          placeholder="Description"
          onChange={(e) => setDescription(e.target.value)}
          onBlur={() =>
            description !== (tag.description ?? "") && save({ description })
          }
          inputProps={{ "aria-label": `Description of ${tag.name}` }}
          sx={{ flex: 1 }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ width: 110 }}
          data-testid="tag-used-on"
        >
          {usedOnText(tag.used_on)}
        </Typography>
        <Tooltip title="Delete tag">
          <IconButton
            size="small"
            aria-label={`Delete ${tag.name}`}
            onClick={() => setConfirm(true)}
          >
            <Iconify icon="mdi:trash-can-outline" width={18} />
          </IconButton>
        </Tooltip>
      </Stack>
      {palette ? (
        <Stack
          direction="row"
          gap={0.5}
          sx={{ mt: 0.75, ml: 0.5 }}
          role="radiogroup"
          aria-label={`Colour of ${tag.name}`}
        >
          {TAG_PALETTE.map((c) => (
            <Tooltip key={c.token} title={c.label}>
              <IconButton
                size="small"
                role="radio"
                aria-checked={c.token === tag.color}
                aria-label={c.label}
                onClick={() => {
                  setPalette(false);
                  if (c.token !== tag.color) save({ color: c.token });
                }}
                sx={{
                  p: 0.5,
                  outline: c.token === tag.color ? "2px solid" : "none",
                  outlineColor: "text.primary",
                }}
              >
                <Box
                  sx={{
                    width: 14,
                    height: 14,
                    borderRadius: "50%",
                    bgcolor: c.hex,
                  }}
                />
              </IconButton>
            </Tooltip>
          ))}
        </Stack>
      ) : null}
      {nameErr || error ? (
        <Typography variant="caption" color="error.main">
          {nameErr || error}
        </Typography>
      ) : null}
      {confirm ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ mt: 1, p: 1, bgcolor: "action.hover", borderRadius: 1 }}
          data-testid="tag-delete-confirm"
        >
          <Typography variant="body2" sx={{ flex: 1 }}>
            {deleteConfirmText(tag)}
          </Typography>
          <Button size="small" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button
            size="small"
            color="error"
            variant="contained"
            disabled={del.isPending}
            onClick={async () => {
              try {
                await del.mutateAsync(tag.id);
              } catch (e) {
                setError(String(tagErrorText(e)));
              }
              setConfirm(false);
            }}
          >
            Delete
          </Button>
        </Stack>
      ) : null}
    </Box>
  );
}

TagRow.propTypes = { tag: PropTypes.object.isRequired, tags: PropTypes.array };

export default function ManageTagsDialog({ open, onClose }) {
  const { data, isLoading } = useTags({ enabled: open });
  const tags = data?.tags ?? [];
  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: { width: 560, maxWidth: "95vw" },
        "data-testid": "manage-tags",
      }}
    >
      <DialogTitle sx={{ pb: 1 }}>Manage tags</DialogTitle>
      <DialogContent dividers sx={{ py: 0 }}>
        <Stack
          direction="row"
          gap={1}
          sx={{ py: 1, color: "text.secondary", fontSize: 12 }}
        >
          <Box sx={{ width: 34 }}>Colour</Box>
          <Box sx={{ width: 150 }}>Name</Box>
          <Box sx={{ flex: 1 }}>Description</Box>
          <Box sx={{ width: 110 }}>Used on</Box>
          <Box sx={{ width: 34 }} />
        </Stack>
        {tags.map((t) => (
          <TagRow
            key={`${t.id}:${t.name}:${t.description}`}
            tag={t}
            tags={tags}
          />
        ))}
        {!tags.length && !isLoading ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
            No tags yet. Create one from any tag picker.
          </Typography>
        ) : null}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  );
}

ManageTagsDialog.propTypes = { open: PropTypes.bool, onClose: PropTypes.func };
