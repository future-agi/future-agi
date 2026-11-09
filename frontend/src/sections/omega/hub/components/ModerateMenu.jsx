import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { ConfirmDialog } from "src/components/custom-dialog";

import {
  MODERATION_CONSEQUENCES,
  MODERATION_NOTE_FIELDS,
  REMOVAL_CATEGORIES,
  UNRANK_REASONS,
} from "../constants";
import { useEntryAction, useLikers, useMaintainerYank } from "../api/queries";
import { formatDateTime } from "../lib/format";

import HubIcon from "./HubIcon";
import MoveDialog from "./MoveDialog";

const ACTIONS = {
  unrank: {
    choice: "reason",
    options: UNRANK_REASONS,
    button: "Unrank",
    consequence: MODERATION_CONSEQUENCES.unrank_entry,
    danger: true,
  },
  rerank: { title: "Rank this entry again", button: "Re-rank" },
  yank: {
    title: "Yank a version",
    choice: "reason-text",
    button: "Yank",
    consequence: MODERATION_CONSEQUENCES.yank_version,
    danger: true,
  },
  remove: {
    choice: "category",
    options: REMOVAL_CATEGORIES,
    button: "Remove",
    consequence: MODERATION_CONSEQUENCES.remove_entry,
    danger: true,
  },
  restore: { title: "Restore this entry", button: "Restore" },
};

// The maintainer-only Moderate menu on an entry page (SPEC §7.1). Each action
// needs a private note, except yank, whose reason is public and emailed.
export default function ModerateMenu({ entry, version }) {
  const [anchor, setAnchor] = useState(null);
  const [action, setAction] = useState(null);
  const [choice, setChoice] = useState("");
  const [note, setNote] = useState("");
  const [showLikers, setShowLikers] = useState(false);
  const [moving, setMoving] = useState(false);
  const publisher = entry.publisher.handle;
  const entryAction = useEntryAction(publisher, entry.name);
  const yank = useMaintainerYank(publisher, entry.name);
  const likers = useLikers(publisher, entry.name, showLikers);
  const spec =
    action && (action !== "yank" || version) ? ACTIONS[action] : null;
  const pending = entryAction.isPending || yank.isPending;
  const error = entryAction.error || yank.error;

  const open = (key) => {
    setAnchor(null);
    if (key === "likers") return setShowLikers(true);
    if (key === "move") return setMoving(true);
    entryAction.reset();
    yank.reset();
    setChoice(ACTIONS[key].options?.[0] || "");
    setNote("");
    return setAction(key);
  };
  const submit = async () => {
    if (pending || !spec || (action === "yank" ? !choice.trim() : !note.trim()))
      return;
    try {
      if (action === "yank")
        await yank.mutateAsync({ version, reason: choice || note });
      else if (action === "unrank")
        await entryAction.mutateAsync({ action, reason: choice, note });
      else if (action === "remove")
        await entryAction.mutateAsync({ action, category: choice, note });
      else await entryAction.mutateAsync({ action, note });
      setAction(null);
    } catch {
      // The dialog shows the error and stays open.
    }
  };
  const items = [
    entry.ranked === false ? ["rerank", "Rank again"] : ["unrank", "Unrank"],
    ...(version ? [["yank", `Yank version ${version}`]] : []),
    ["remove", "Remove"],
    ["move", "Move or rename"],
    ["likers", "Show likers"],
  ];
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<HubIcon name="shield" size={14} />}
        data-testid="hub-moderate-button"
        sx={{ borderColor: "divider" }}
      >
        Moderate
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {items.map(([key, label]) => (
          <MenuItem
            key={key}
            onClick={() => open(key)}
            sx={{ typography: "s2" }}
          >
            {label}
          </MenuItem>
        ))}
      </Menu>
      <ConfirmDialog
        open={Boolean(spec)}
        onClose={() => !pending && setAction(null)}
        closeDisabled={pending}
        title={
          action === "yank" && version
            ? `Yank version ${publisher}/${entry.name}@${version}?`
            : spec?.danger
              ? `${spec.button} ${publisher}/${entry.name}?`
              : spec?.title
        }
        content={
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {spec?.consequence && (
              <Typography typography="s2">{spec.consequence}</Typography>
            )}
            {spec?.choice === "reason" || spec?.choice === "category" ? (
              <TextField
                select
                size="small"
                label={spec.choice === "reason" ? "Reason" : "Category"}
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
              >
                {spec.options.map((option) => (
                  <MenuItem key={option} value={option}>
                    {option}
                  </MenuItem>
                ))}
              </TextField>
            ) : null}
            {spec?.choice === "reason-text" && (
              <TextField
                size="small"
                label={MODERATION_NOTE_FIELDS.yank_version.label}
                helperText={MODERATION_NOTE_FIELDS.yank_version.helperText}
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
              />
            )}
            {action !== "yank" && (
              <TextField
                label={MODERATION_NOTE_FIELDS.private.label}
                helperText={MODERATION_NOTE_FIELDS.private.helperText}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                multiline
                minRows={3}
              />
            )}
            {error && <Alert severity="error">{error.message}</Alert>}
          </Box>
        }
        action={
          <Button
            variant="contained"
            color={spec?.danger ? "error" : "primary"}
            disabled={
              pending || (action === "yank" ? !choice.trim() : !note.trim())
            }
            onClick={submit}
          >
            {spec?.button}
          </Button>
        }
      />
      <MoveDialog
        open={moving}
        onClose={() => setMoving(false)}
        entry={entry}
      />
      <Dialog
        open={showLikers}
        onClose={() => setShowLikers(false)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Likers (maintainers only)</DialogTitle>
        <DialogContent>
          {(likers.data || []).length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              {likers.isLoading ? "Loading…" : "Nobody has liked it yet."}
            </Typography>
          ) : (
            <Box component="ul" sx={{ m: 0, pl: 2 }}>
              {likers.data.map((row) => (
                <li key={row.user}>
                  <Typography typography="s2">{`${row.user} · ${formatDateTime(row.at)}`}</Typography>
                </li>
              ))}
            </Box>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

ModerateMenu.propTypes = {
  entry: PropTypes.object.isRequired,
  version: PropTypes.string,
};
