import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { SAVED_VIEWS_NOT_STORED, useSavedViews } from "src/api/develop/r3";
import { gridViewHref } from "src/sections/develop-detail/DataTab/DataGrid/gridViewLink";
import { copyText } from "./CopyButton";
import { notify } from "./toast";

const same = (a, b) => JSON.stringify(a ?? {}) === JSON.stringify(b ?? {});

/** Keys of a saved view's state that are never URL params: `layout` (one
 * view, ONE-VIEW-SPEC §3 persistence) and the legacy `ui` mode (G-10). */
export const NON_URL_VIEW_KEYS = Object.freeze(["layout", "ui"]);

/** A saved view's URL state: `ui` (legacy) and `layout` removed. Pure. */
export function viewUrlState(state) {
  const out = { ...(state ?? {}) };
  NON_URL_VIEW_KEYS.forEach((k) => delete out[k]);
  return out;
}

/** The layout a saved view carries (one view), or null. */
export const viewLayout = (state) =>
  state?.layout && typeof state.layout === "object" ? state.layout : null;

/**
 * The current URL with a view's state written as params (CSV arrays). The
 * legacy `ui` param is dropped; the layout never goes into the URL.
 */
export function viewHref(state) {
  const url = new URL(window.location.href);
  url.searchParams.delete("ui");
  Object.entries(viewUrlState(state)).forEach(([k, v]) => {
    if (
      v === null ||
      v === undefined ||
      v === "" ||
      (Array.isArray(v) && !v.length)
    )
      url.searchParams.delete(k);
    else if (typeof v === "boolean") url.searchParams.set(k, v ? "1" : "0");
    else url.searchParams.set(k, Array.isArray(v) ? v.join(",") : String(v));
  });
  return url.toString();
}

/** W9 A0: the tooltip on the disabled Share checkbox for a viewer. */
export const VIEWER_SHARE_REASON =
  "Viewers can save views for themselves only.";

/** A saved view's caption: `Shared · <created_by>` or `by <created_by>`. */
export const savedViewCaption = (v) =>
  v?.shared ? `Shared · ${v.created_by}` : `by ${v?.created_by ?? ""}`;

/** Delete is offered on the caller's own views, or to an admin (server). */
export const canDeleteView = (v) => v?.can_delete ?? v?.mine ?? true;

/** A saved view's name, with `Shared · <created_by>` under a shared one. */
export function SavedViewLabel({ view }) {
  if (!view?.shared) return view?.name ?? null;
  return (
    <Box
      component="span"
      sx={{ display: "block", minWidth: 0, textAlign: "left" }}
    >
      <Box
        component="span"
        sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {view.name}
      </Box>
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        sx={{ display: "block" }}
      >
        {savedViewCaption(view)}
      </Typography>
    </Box>
  );
}
SavedViewLabel.propTypes = { view: PropTypes.object };

/**
 * Delete a saved view, then the `View "<name>" deleted` toast whose Undo
 * restores that same record (id, author, created_at, shared and state), so
 * an admin undoing a delete of someone else's shared view gives it back to
 * its author. A refused restore says why in an error toast.
 * @param {object} view  a listed SavedView
 * @param {{remove: (id) => Promise, restore: (id) => Promise,
 *   toast?: typeof notify}} deps
 */
export async function deleteViewWithUndo(view, { remove, restore, toast }) {
  const say = toast ?? notify;
  await remove(view.id);
  say(`View "${view.name}" deleted`, {
    variant: "default",
    key: "view-deleted",
    undo: {
      onUndo: () =>
        Promise.resolve()
          .then(() => restore(view.id))
          .catch((e) =>
            say(
              e?.response?.data?.result ??
                e?.result ??
                `Couldn't bring back "${view.name}". Try again.`,
              { variant: "error", key: "view-restore" },
            ),
          ),
    },
  });
}

/**
 * The saved-views model shared by SavedViewsMenu and the one-view ViewMenu:
 * the user's views of this surface + scope, which one is active, save /
 * delete (Undo) / apply / copy link. `layout` (optional) is stored with a
 * saved view and handed back to `onApplyLayout` on apply; `ui` is stripped.
 */
export function useSavedViewsModel({
  surface,
  scopeId,
  state,
  layout,
  onApply,
  onApplyLayout,
  isModified,
}) {
  const {
    views,
    create,
    remove,
    restore,
    isSaving,
    isLoading,
    loadError,
    notStored,
    reload,
  } = useSavedViews(surface, scopeId);
  const urlState = useMemo(() => viewUrlState(state), [state]);
  const active = useMemo(
    () =>
      views.find(
        (v) =>
          same(viewUrlState(v.state), urlState) &&
          (!layout ||
            !viewLayout(v.state) ||
            same(viewLayout(v.state), layout)),
      ) ?? null,
    [views, urlState, layout],
  );
  const modified = isModified ?? !active;
  const save = async (name, { shared = false } = {}) => {
    const n = String(name ?? "").trim();
    if (!n) throw new Error("Give the view a name.");
    await create(n, layout ? { ...urlState, layout } : urlState, { shared });
    notify(
      shared
        ? `View "${n}" saved and shared with the workspace`
        : `View "${n}" saved`,
      { variant: "success", key: "view-saved" },
    );
  };
  const del = (view) => deleteViewWithUndo(view, { remove, restore });
  const apply = (view) => {
    onApply?.(viewUrlState(view.state));
    const l = viewLayout(view.state);
    if (l) onApplyLayout?.(l);
  };
  const copyLink = async (view) => {
    const href =
      surface === "grid"
        ? gridViewHref(
            view?.state ?? urlState,
            view?.state?.layout ?? layout,
            window.location.href,
          )
        : viewHref(view ? view.state : urlState);
    if (await copyText(href))
      notify("Link copied", { variant: "success", key: "view-link" });
  };
  return {
    views,
    active,
    modified,
    isSaving,
    isLoading,
    loadError,
    notStored,
    reload,
    save,
    del,
    apply,
    copyLink,
  };
}

/**
 * The `Save this view` name dialog. `canShare` (W9 A0): undefined = no Share
 * checkbox; true = the checkbox; false = the checkbox disabled with the
 * viewer reason.
 */
export function SaveViewDialog({ open, onClose, onSave, isSaving, canShare }) {
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (open) {
      setName("");
      setShared(false);
      setError(null);
    }
  }, [open]);
  const save = async () => {
    if (!name.trim()) {
      setError("Give the view a name.");
      return;
    }
    try {
      await onSave(name, { shared: canShare === true && shared });
      onClose();
    } catch (e) {
      setError(
        e?.response?.data?.result ??
          (e?.message === "Give the view a name." ||
          e?.message === SAVED_VIEWS_NOT_STORED
            ? e.message
            : "Couldn't save the view. Try again."),
      );
    }
  };
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}>
        Save this view
      </DialogTitle>
      <DialogContent>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 1.5 }}>
          {canShare === true && shared
            ? "Saves the filters, columns and layout you see now. Everyone in this workspace sees it."
            : "Saves the filters, columns and layout you see now. Only you see it."}
        </Typography>
        <TextField
          autoFocus
          fullWidth
          size="small"
          label="Name"
          value={name}
          error={Boolean(error)}
          helperText={error ?? " "}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
          }}
        />
        {canShare !== undefined && (
          <CustomTooltip
            show={canShare === false}
            size="small"
            title={VIEWER_SHARE_REASON}
            placement="bottom-start"
          >
            <span>
              <FormControlLabel
                disabled={canShare === false}
                control={
                  <Checkbox
                    size="small"
                    checked={canShare === true && shared}
                    onChange={(e) => setShared(e.target.checked)}
                  />
                }
                label="Share with everyone in this workspace"
                componentsProps={{ typography: { typography: "s2" } }}
              />
            </span>
          </CustomTooltip>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button variant="outlined" color="inherit" onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton variant="contained" onClick={save} loading={isSaving}>
          Save view
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

SaveViewDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onSave: PropTypes.func,
  isSaving: PropTypes.bool,
  canShare: PropTypes.bool,
};

/**
 * SavedViewsMenu — `Views ▾` with a `Modified` dot; `Reset`, `Save as…`
 * (name dialog) and the user's saved views of this surface + scope, each
 * with `Copy link` and delete. Backed by useSavedViews (r3.js).
 *
 * Props (frozen; `isModified`, `onReset` optional additions):
 * @param {string} props.surface  keymap-style surface (`grid`, `matrix`…)
 * @param {string} [props.scopeId]  usually the dataset id
 * @param {object} props.state  the current useViewUrlState values
 * @param {(state) => void} props.onApply  apply a saved view's state
 * @param {boolean} [props.isModified]  current view differs from default
 * @param {Function} [props.onReset]
 * @param {object} [props.layout]  one view: the layout object saved with a
 *   view (columns, sections, density); `ui` is never saved or applied
 * @param {(layout) => void} [props.onApplyLayout]
 * @param {boolean} [props.canShare]  W9 A0: offer "Share with everyone in
 *   this workspace" (false: shown disabled, for a viewer)
 */
export default function SavedViewsMenu({
  surface,
  scopeId,
  state,
  onApply,
  isModified,
  onReset,
  layout,
  onApplyLayout,
  canShare,
}) {
  const {
    views,
    active,
    modified,
    isSaving,
    notStored,
    save,
    del,
    apply,
    copyLink,
  } = useSavedViewsModel({
    surface,
    scopeId,
    state,
    layout,
    onApply,
    onApplyLayout,
    isModified,
  });
  const [anchor, setAnchor] = useState(null);
  const [naming, setNaming] = useState(false);

  return (
    <>
      <CustomTooltip
        show
        size="small"
        title={
          active
            ? `Showing the saved view "${active.name}"`
            : modified
              ? "This view differs from the default. Save it to come back to it."
              : "Saved views of this page"
        }
      >
        <Button
          size="small"
          variant="text"
          color="inherit"
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mdi:bookmark-outline" width={16} />}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{
            typography: "s2",
            color: "text.secondary",
            position: "relative",
          }}
        >
          {active ? active.name : "Views"}
          {modified && !active && (
            <Box
              component="span"
              aria-label="Modified"
              sx={{
                ml: 0.75,
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: "amber.500",
                display: "inline-block",
              }}
            />
          )}
        </Button>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        PaperProps={{ sx: { minWidth: 260, maxWidth: 360 } }}
      >
        {onReset && (
          <MenuItem
            disabled={!modified}
            onClick={() => {
              setAnchor(null);
              onReset();
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:restore" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Reset to default
            </ListItemText>
          </MenuItem>
        )}
        <MenuItem
          disabled={notStored}
          onClick={() => {
            setAnchor(null);
            setNaming(true);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:content-save-outline" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Save as…
          </ListItemText>
        </MenuItem>
        <Divider />
        {views.length === 0 && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 2, py: 1 }}
          >
            {notStored
              ? SAVED_VIEWS_NOT_STORED
              : "No saved views yet. Save this one to come back to it."}
          </Typography>
        )}
        {views.map((v) => (
          <MenuItem
            key={v.id}
            selected={active?.id === v.id}
            onClick={() => {
              setAnchor(null);
              apply(v);
            }}
            sx={{ pr: 0.5 }}
          >
            <ListItemText
              primaryTypographyProps={{ typography: "s1", noWrap: true }}
              secondaryTypographyProps={{ typography: "s3" }}
              secondary={savedViewCaption(v)}
            >
              {v.name}
            </ListItemText>
            <CustomTooltip show size="small" title="Copy link">
              <IconButton
                size="small"
                aria-label={`Copy link to ${v.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  copyLink(v);
                }}
              >
                <Iconify icon="mdi:link-variant" width={16} />
              </IconButton>
            </CustomTooltip>
            {canDeleteView(v) && (
              <CustomTooltip show size="small" title="Delete view">
                <IconButton
                  size="small"
                  aria-label={`Delete ${v.name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    del(v);
                  }}
                >
                  <Iconify icon="mdi:trash-can-outline" width={16} />
                </IconButton>
              </CustomTooltip>
            )}
          </MenuItem>
        ))}
      </Menu>
      <SaveViewDialog
        open={naming}
        onClose={() => setNaming(false)}
        onSave={save}
        isSaving={isSaving}
        canShare={canShare}
      />
    </>
  );
}

SavedViewsMenu.propTypes = {
  surface: PropTypes.string.isRequired,
  scopeId: PropTypes.string,
  state: PropTypes.object,
  onApply: PropTypes.func,
  isModified: PropTypes.bool,
  onReset: PropTypes.func,
  layout: PropTypes.object,
  onApplyLayout: PropTypes.func,
  canShare: PropTypes.bool,
};
