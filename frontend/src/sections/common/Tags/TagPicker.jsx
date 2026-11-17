// W5-3 (W5-SCOPE §7 C1): pick tags for one or more items. Search; each tag
// with its colour dot, checked when every item has it (a dash when some do);
// `Create tag “candidate”`; `Manage tags…` at the foot.
//
//   <TagPicker anchorEl={el} onClose={...} itemTagIds={[[...ids], ...]}
//              onChange={(nextLists, tag) => ...} />
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  Divider,
  InputBase,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  canCreateTag,
  matchTags,
  normaliseTagName,
  tagColor,
  toggleTagAcross,
} from "./tagModel";
import { tagErrorText, useCreateTag, useTags } from "./useTags";
import ManageTagsDialog from "./ManageTagsDialog";

export default function TagPicker({
  anchorEl,
  onClose,
  itemTagIds,
  onChange,
  title,
}) {
  const open = Boolean(anchorEl);
  const { data } = useTags({ enabled: open });
  const tags = useMemo(() => data?.tags ?? [], [data?.tags]);
  const [query, setQuery] = useState("");
  const [manage, setManage] = useState(false);
  const [error, setError] = useState("");
  const create = useCreateTag();
  const lists = itemTagIds ?? [];
  const shown = matchTags(tags, query);
  const stateOf = (id) => {
    const n = lists.filter((l) => (l ?? []).includes(id)).length;
    return n === 0 ? "none" : n === lists.length ? "all" : "some";
  };
  const toggle = (tag) => onChange?.(toggleTagAcross(lists, tag.id), tag);
  const onCreate = async () => {
    setError("");
    try {
      const res = await create.mutateAsync({ name: normaliseTagName(query) });
      const tag = res?.data?.result;
      if (tag?.id) {
        onChange?.(toggleTagAcross(lists, tag.id), tag);
        setQuery("");
      }
    } catch (e) {
      setError(String(tagErrorText(e)));
    }
  };

  return (
    <>
      <Popover
        open={open && !manage}
        anchorEl={anchorEl}
        onClose={onClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: { sx: { width: 280 }, "data-testid": "tag-picker" },
        }}
      >
        {title ? (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", px: 1.5, pt: 1 }}
          >
            {title}
          </Typography>
        ) : null}
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 1 }}
        >
          <Iconify
            icon="mdi:magnify"
            width={16}
            sx={{ color: "text.secondary" }}
          />
          <InputBase
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                canCreateTag(tags, query) &&
                !shown.length
              )
                onCreate();
            }}
            placeholder="Search or create a tag"
            inputProps={{ "aria-label": "Search tags" }}
            sx={{ flex: 1, fontSize: 13 }}
          />
        </Box>
        <Divider />
        <List dense sx={{ maxHeight: 260, overflow: "auto", py: 0.5 }}>
          {shown.map((t) => {
            const st = stateOf(t.id);
            return (
              <ListItemButton
                key={t.id}
                onClick={() => toggle(t)}
                data-testid="tag-option"
                data-tag-name={t.name}
              >
                <ListItemIcon sx={{ minWidth: 30 }}>
                  <Checkbox
                    size="small"
                    edge="start"
                    checked={st === "all"}
                    indeterminate={st === "some"}
                    tabIndex={-1}
                    disableRipple
                    inputProps={{ "aria-label": t.name }}
                  />
                </ListItemIcon>
                <Box
                  sx={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    bgcolor: tagColor(t.color),
                    mr: 1,
                  }}
                />
                <ListItemText
                  primary={t.name}
                  primaryTypographyProps={{ fontSize: 13, noWrap: true }}
                />
              </ListItemButton>
            );
          })}
          {canCreateTag(tags, query) ? (
            <ListItemButton
              onClick={onCreate}
              disabled={create.isPending}
              data-testid="tag-create"
            >
              <ListItemIcon sx={{ minWidth: 30 }}>
                <Iconify icon="mdi:plus" width={18} />
              </ListItemIcon>
              <ListItemText
                primary={`Create tag “${normaliseTagName(query)}”`}
                primaryTypographyProps={{ fontSize: 13 }}
              />
            </ListItemButton>
          ) : null}
          {!shown.length && !canCreateTag(tags, query) ? (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ px: 2, py: 1 }}
            >
              {tags.length
                ? "No tag matches."
                : "No tags yet. Type a name to create one."}
            </Typography>
          ) : null}
        </List>
        {error ? (
          <Typography
            variant="caption"
            color="error.main"
            sx={{ px: 1.5, display: "block" }}
          >
            {error}
          </Typography>
        ) : null}
        <Divider />
        <ListItemButton onClick={() => setManage(true)} sx={{ py: 1 }}>
          <ListItemText
            primary="Manage tags…"
            primaryTypographyProps={{ fontSize: 13 }}
          />
        </ListItemButton>
      </Popover>
      <ManageTagsDialog open={manage} onClose={() => setManage(false)} />
    </>
  );
}

TagPicker.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  itemTagIds: PropTypes.arrayOf(PropTypes.array),
  onChange: PropTypes.func,
  title: PropTypes.string,
};
