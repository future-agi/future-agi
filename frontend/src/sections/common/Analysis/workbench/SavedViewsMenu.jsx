import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import {
  SAVED_VIEWS_KEY,
  useCreateWorkspaceSavedView,
  useDeleteWorkspaceSavedView,
  useDuplicateSavedView,
  useGetWorkspaceSavedViews,
  useUpdateWorkspaceSavedView,
} from "src/api/project/saved-views";
import { WORKBENCH_VIEW_TAB } from "src/api/develop/r2";
import { SEGMENTED_SX } from "src/sections/experiment-detail/ExperimentBarRightSection/ExperimentBarSummaryRightSection";

const DUP = "A view with this name exists";

const listOf = (raw) =>
  Array.isArray(raw) ? raw : raw?.custom_views ?? raw?.results ?? [];
const datasetOf = (v) =>
  v?.config?.display?.dataset_id ?? v?.config?.dataset_id;

/** Save / rename dialog: name + `Only me | Workspace`. */
function NameDialog({
  open,
  title,
  initialName,
  initialVisibility,
  taken,
  busy,
  onClose,
  onSubmit,
  withVisibility,
}) {
  const [name, setName] = useState(initialName);
  const [visibility, setVisibility] = useState(initialVisibility ?? "personal");
  const trimmed = name.trim();
  const dup =
    trimmed && trimmed !== initialName?.trim?.() && taken.includes(trimmed);
  const submit = () =>
    !dup && trimmed && onSubmit({ name: trimmed, visibility });
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m3", pb: 1 }}>{title}</DialogTitle>
      <DialogContent>
        <Stack gap={1.5} sx={{ pt: 0.5 }}>
          <TextField
            autoFocus
            size="small"
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            error={Boolean(dup)}
            helperText={dup ? DUP : " "}
          />
          {withVisibility && (
            <Stack gap={0.5}>
              <Typography typography="s2" color="text.secondary">
                Who can see it
              </Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={visibility}
                onChange={(_, v) => v && setVisibility(v)}
                sx={SEGMENTED_SX}
              >
                <ToggleButton value="personal">Only me</ToggleButton>
                <ToggleButton value="project">Workspace</ToggleButton>
              </ToggleButtonGroup>
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!trimmed || Boolean(dup) || busy}
          onClick={submit}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}

NameDialog.propTypes = {
  open: PropTypes.bool,
  title: PropTypes.string,
  initialName: PropTypes.string,
  initialVisibility: PropTypes.string,
  taken: PropTypes.array,
  busy: PropTypes.bool,
  onClose: PropTypes.func,
  onSubmit: PropTypes.func,
  withVisibility: PropTypes.bool,
};

/**
 * Saved Workbench views (R2-SPEC §6.3.7) through the workspace saved-view
 * API (`tab_type=dataset_workbench`, config `{display:{dataset_id, …}}`):
 * `Saved views ▾` (apply · rename · duplicate · delete) + `Save this view`.
 * Runs are never saved; applying keeps the runs in the URL.
 */
const SavedViewsMenu = ({ datasetId, display, defaultName, onApply }) => {
  const qc = useQueryClient();
  const list = useGetWorkspaceSavedViews(WORKBENCH_VIEW_TAB);
  const create = useCreateWorkspaceSavedView(WORKBENCH_VIEW_TAB);
  const update = useUpdateWorkspaceSavedView(WORKBENCH_VIEW_TAB);
  const remove = useDeleteWorkspaceSavedView(WORKBENCH_VIEW_TAB);
  const duplicate = useDuplicateSavedView(undefined);
  const [anchor, setAnchor] = useState(null);
  const [itemMenu, setItemMenu] = useState(null);
  const [dialog, setDialog] = useState(null);
  const all = useMemo(() => listOf(list.data), [list.data]);
  const views = all.filter((v) => datasetOf(v) === datasetId);
  const taken = all.map((v) => v.name);
  const refresh = () =>
    qc.invalidateQueries({
      queryKey: [SAVED_VIEWS_KEY, "workspace", WORKBENCH_VIEW_TAB],
    });
  const fail = (e) =>
    enqueueSnackbar(
      typeof e?.result === "string"
        ? e.result
        : e?.message ?? "Could not save the view",
      { variant: "error" },
    );

  const save = ({ name, visibility }) =>
    create
      .mutateAsync({
        name,
        visibility,
        config: { display: { ...display, dataset_id: datasetId } },
      })
      .then(() => {
        setDialog(null);
        enqueueSnackbar("View saved", { variant: "success" });
      })
      .catch(fail);

  return (
    <>
      <Stack direction="row" gap={0.75}>
        <Button
          size="small"
          variant="outlined"
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{
            flex: 1,
            typography: "s3",
            whiteSpace: "nowrap",
            px: 1,
            minWidth: 0,
          }}
        >
          Saved views
        </Button>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setDialog({ kind: "save" })}
          sx={{
            flex: 1,
            typography: "s3",
            whiteSpace: "nowrap",
            px: 1,
            minWidth: 0,
          }}
        >
          Save this view
        </Button>
      </Stack>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 300 } } }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 2, py: 0.5 }}
        >
          Views on this dataset. Your open runs stay.
        </Typography>
        {views.length === 0 && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ px: 2, py: 1 }}
          >
            No saved views yet.
          </Typography>
        )}
        {views.map((v) => (
          <MenuItem
            key={v.id}
            onClick={() => {
              setAnchor(null);
              onApply(v.config?.display ?? {}, v.name);
            }}
            sx={{ gap: 1 }}
          >
            <ListItemIcon sx={{ minWidth: "24px !important" }}>
              <Iconify
                icon={
                  v.visibility === "project"
                    ? "mdi:account-group-outline"
                    : "mdi:lock-outline"
                }
                width={16}
              />
            </ListItemIcon>
            <ListItemText
              primaryTypographyProps={{ typography: "s1", noWrap: true }}
            >
              {v.name}
            </ListItemText>
            <IconButton
              size="small"
              aria-label={`More for ${v.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setItemMenu({ anchor: e.currentTarget, view: v });
              }}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="mdi:dots-horizontal" width={16} />
            </IconButton>
          </MenuItem>
        ))}
      </Menu>
      <Menu
        anchorEl={itemMenu?.anchor}
        open={Boolean(itemMenu)}
        onClose={() => setItemMenu(null)}
      >
        <MenuItem
          onClick={() => {
            setDialog({ kind: "rename", view: itemMenu.view });
            setItemMenu(null);
          }}
          sx={{ typography: "s1" }}
        >
          Rename
        </MenuItem>
        <MenuItem
          onClick={() => {
            const v = itemMenu.view;
            setItemMenu(null);
            duplicate
              .mutateAsync({ id: v.id, name: `${v.name} (copy)` })
              .then(() => {
                refresh();
                enqueueSnackbar("View duplicated", { variant: "success" });
              })
              .catch(fail);
          }}
          sx={{ typography: "s1" }}
        >
          Duplicate
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            setDialog({ kind: "delete", view: itemMenu.view });
            setItemMenu(null);
          }}
          sx={{ typography: "s1", color: "error.main" }}
        >
          Delete
        </MenuItem>
      </Menu>
      {dialog?.kind === "save" && (
        <NameDialog
          open
          withVisibility
          title="Save this view"
          initialName={defaultName}
          taken={taken}
          busy={create.isPending}
          onClose={() => setDialog(null)}
          onSubmit={save}
        />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          open
          title="Rename view"
          initialName={dialog.view.name}
          taken={taken}
          busy={update.isPending}
          onClose={() => setDialog(null)}
          onSubmit={({ name }) =>
            update
              .mutateAsync({ id: dialog.view.id, name })
              .then(() => {
                setDialog(null);
                enqueueSnackbar("View renamed", { variant: "success" });
              })
              .catch(fail)
          }
        />
      )}
      {dialog?.kind === "delete" && (
        <Dialog open onClose={() => setDialog(null)} maxWidth="xs" fullWidth>
          <DialogTitle sx={{ typography: "m3" }}>
            Delete “{dialog.view.name}”?
          </DialogTitle>
          <DialogContent>
            <Typography typography="s1" color="text.secondary">
              The view goes for everyone who can see it. Your runs and rows
              stay.
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setDialog(null)}>Cancel</Button>
            <Button
              color="error"
              variant="contained"
              disabled={remove.isPending}
              onClick={() =>
                remove
                  .mutateAsync(dialog.view.id)
                  .then(() => {
                    setDialog(null);
                    enqueueSnackbar("View deleted", { variant: "success" });
                  })
                  .catch(fail)
              }
            >
              Delete
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </>
  );
};

SavedViewsMenu.propTypes = {
  datasetId: PropTypes.string.isRequired,
  display: PropTypes.object.isRequired,
  defaultName: PropTypes.string,
  onApply: PropTypes.func.isRequired,
};

export default SavedViewsMenu;
