import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { isPrototypeMode } from "src/utils/prototype";
import TagPicker from "src/sections/common/Tags/TagPicker";
import TagChips from "src/sections/common/Tags/TagChips";
import { tagsByIds } from "src/sections/common/Tags/tagModel";
import { useTags } from "src/sections/common/Tags/useTags";

// HO D5 (W5 C-H8): where the workspace tag registry is served (the integration
// /model-hub/tags/ paths), tags are picked with the one TagPicker (search,
// registry colours, Create tag, Manage tags…); elsewhere they are typed.
const ROW_TAG_REGISTRY = isPrototypeMode();

const splitTags = (text) => [
  ...new Set(
    String(text ?? "")
      .split(/[,;|]/)
      .map((t) => t.trim())
      .filter(Boolean),
  ),
];

/**
 * W3-1 selection `Tag…`: add or remove row tags (`metadata.tags`) on the
 * selected rows, as a pending change (Save version keeps it).
 */
export default function TagRowsDialog({
  open,
  count,
  onClose,
  onApply,
  registry = ROW_TAG_REGISTRY,
}) {
  const [mode, setMode] = useState("add");
  const [text, setText] = useState("");
  const [picked, setPicked] = useState([]);
  const [anchor, setAnchor] = useState(null);
  const { data } = useTags({ enabled: Boolean(open && registry) });
  const pickedTags = useMemo(
    () => tagsByIds(data?.tags ?? [], picked),
    [data?.tags, picked],
  );
  useEffect(() => {
    if (open) {
      setMode("add");
      setText("");
      setPicked([]);
      setAnchor(null);
    }
  }, [open]);
  const tags = registry ? pickedTags.map((t) => t.name) : splitTags(text);
  const blocked = tags.length
    ? null
    : registry
      ? "Choose a tag"
      : "Type a tag (split several with , ; or |)";
  const apply = () => {
    if (blocked) return;
    onApply(
      mode === "add" ? { add: tags, remove: [] } : { add: [], remove: tags },
    );
  };
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          {`Tag ${count === 1 ? "1 row" : `${count} rows`}`}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={mode}
          onChange={(_, v) => v && setMode(v)}
          sx={{ mb: 1.5 }}
        >
          <ToggleButton value="add" sx={{ typography: "s3", px: 1.5 }}>
            Add tags
          </ToggleButton>
          <ToggleButton value="remove" sx={{ typography: "s3", px: 1.5 }}>
            Remove tags
          </ToggleButton>
        </ToggleButtonGroup>
        {registry ? (
          <Box data-testid="tag-rows-picked">
            <TagChips tags={pickedTags} size="medium" />
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mdi:tag-outline" width={16} />}
              onClick={(e) => setAnchor(e.currentTarget)}
              sx={{ mt: pickedTags.length ? 1 : 0 }}
              data-testid="tag-rows-choose"
            >
              {pickedTags.length ? "Change tags" : "Choose tags"}
            </Button>
            <TagPicker
              anchorEl={anchor}
              onClose={() => setAnchor(null)}
              itemTagIds={[picked]}
              onChange={(lists) => setPicked(lists[0] ?? [])}
              title={mode === "add" ? "Tags to add" : "Tags to remove"}
            />
          </Box>
        ) : (
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="Tags"
            placeholder="refund, vip"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") apply();
            }}
            inputProps={{ "data-testid": "tag-rows-input", maxLength: 200 }}
          />
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <CustomTooltip
          show={Boolean(blocked)}
          title={blocked ?? ""}
          size="small"
        >
          <Box component="span">
            <Button
              variant="contained"
              disabled={Boolean(blocked)}
              onClick={apply}
              data-testid="tag-rows-apply"
            >
              {mode === "add" ? "Add tags" : "Remove tags"}
            </Button>
          </Box>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
}

TagRowsDialog.propTypes = {
  open: PropTypes.bool,
  count: PropTypes.number,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  registry: PropTypes.bool,
};
