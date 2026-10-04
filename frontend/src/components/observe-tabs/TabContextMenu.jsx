import React, { useState } from "react";
import PropTypes from "prop-types";
import { Menu, MenuItem, ListItemIcon, ListItemText, Divider, Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useUpdateSavedView, useDuplicateSavedView } from "src/api/project/saved-views";
import DeleteViewDialog from "./DeleteViewDialog";
import ShareViewDialog from "./ShareViewDialog";

const TabContextMenu = ({ anchorPosition, view, projectId, projectName = "this project", isDirty = false, activeTab, onClose, onRename, onTabChange }) => {
  const [dialog, setDialog] = useState(null);
  const [error, setError] = useState(null);
  const { mutate: updateView, isPending } = useUpdateSavedView(projectId);
  const { mutate: duplicateView } = useDuplicateSavedView(projectId);
  if (!view || !anchorPosition) return null;
  const finish = () => {
    setDialog(null);
    onClose();
    const refocus = () => document.querySelector(`[data-view-id="${view.id}"]`)?.focus();
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(refocus);
    else setTimeout(refocus, 0);
  };
  const duplicate = () => {
    duplicateView({ id: view.id }, {
      onSuccess: (res) => { if (res.data?.result?.id) onTabChange(`view-${res.data.result.id}`); },
      onError: (err) => enqueueSnackbar(getRequestErrorMessage(err, "Failed to duplicate view"), { variant: "error" }),
    });
    finish();
  };
  const share = () => {
    if (!view.can_edit || isPending) return;
    updateView({ id: view.id, expected_revision: view.revision, visibility: view.visibility === "project" ? "personal" : "project" }, {
      onSuccess: finish,
      onError: (err) => setError(getRequestErrorMessage(err, "Could not change sharing. Please retry.")),
    });
  };
  const copyLink = async () => {
    const url = new URL(`/dashboard/observe/${projectId}/llm-tracing`, window.location.origin);
    url.searchParams.set("tab", `view-${view.id}`);
    try {
      await navigator.clipboard.writeText(url.toString());
      enqueueSnackbar("Link copied", { variant: "success" });
    } catch {
      enqueueSnackbar("Could not copy link", { variant: "error" });
    }
    finish();
  };
  const item = (label, icon, action) => <MenuItem onClick={action} dense><ListItemIcon><Iconify icon={icon} width={18} /></ListItemIcon><ListItemText>{label}</ListItemText></MenuItem>;
  return <>
    <Menu open={!dialog} onClose={finish} anchorReference="anchorPosition" anchorPosition={{ top: anchorPosition.y, left: anchorPosition.x }} PaperProps={{ sx: { minWidth: 210 } }}>
      {view.can_edit && item("Rename", "mdi:pencil-outline", () => { onClose(); onRename(view.id); })}
      {item(view.is_owner ? "Duplicate" : "Save a copy", "mdi:content-copy", duplicate)}
      {view.can_edit && <Divider />}
      {view.can_edit && item(view.visibility === "project" ? "Make personal" : "Share with project", view.visibility === "project" ? "mdi:lock-outline" : "mdi:account-group-outline", () => { setError(null); setDialog("share"); })}
      {item("Copy link", "mdi:link-variant", copyLink)}
      {view.can_delete && <Divider />}
      {view.can_delete && <MenuItem onClick={() => setDialog("delete")} dense sx={{ color: "error.main" }}><ListItemIcon sx={{ color: "inherit" }}><Iconify icon="mdi:delete-outline" width={18} /></ListItemIcon><ListItemText>Delete</ListItemText>{!view.is_owner && <Chip label="Admin" size="small" color="primary" variant="outlined" />}</MenuItem>}
    </Menu>
    {dialog === "share" && <ShareViewDialog view={view} projectName={projectName} dirty={isDirty} pending={isPending} error={error} onClose={finish} onSaveFirst={finish} onConfirm={share} />}
    {dialog === "delete" && <DeleteViewDialog view={view} projectId={projectId} projectName={projectName} onClose={finish} onDeleted={(id) => { if (activeTab === `view-${id}`) onTabChange("traces"); }} />}
  </>;
};
TabContextMenu.propTypes = { anchorPosition: PropTypes.object, view: PropTypes.object, projectId: PropTypes.string, projectName: PropTypes.string, isDirty: PropTypes.bool, activeTab: PropTypes.string, onClose: PropTypes.func.isRequired, onRename: PropTypes.func.isRequired, onTabChange: PropTypes.func.isRequired };
export default TabContextMenu;
