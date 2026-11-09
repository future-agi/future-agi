import React from "react";
import PropTypes from "prop-types";
import { Button, Typography } from "@mui/material";
import { create } from "zustand";
import { useQueryClient } from "@tanstack/react-query";
import { rowViewKeys } from "src/api/develop/row-view";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { notify } from "src/sections/common/ux/toast";
import {
  useAsOfVersion,
  useSaveDatasetVersion,
} from "src/api/develop/dataset-versions";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useDevelopSelectedRowsStore } from "../../states";
import { useChangeSet } from "./changeSetStore";
import useVersionUrl from "./useVersionUrl";
import { RESTORE_BLOCKED_PENDING, restoreRowsText } from "./versionText";

/**
 * Which rows `Restore rows…` is confirming (one host: the Row view, which
 * is mounted with the Data tab). `request` = `{rows: [{row_id, n}], from}`.
 */
export const useRestoreRowsDialog = create((set) => ({
  request: null,
  open: (request) => set({ request: request?.rows?.length ? request : null }),
  close: () => set({ request: null }),
}));

/**
 * integration review: after a restore the open screen re-reads what it shows.
 * The selection is cleared (its rows are no longer deleted), the as-of
 * grid re-reads its rows (purged, as AsOfBanner does on a view change, so
 * no row stays struck), and the open Row view and the select-all read ask
 * again (its banner and Restore come from the fresh row).
 */
export function afterRestore({ queryClient, gridApi, datasetId }) {
  useDevelopSelectedRowsStore.setState({ toggledNodes: [], selectAll: false });
  gridApi?.current?.deselectAll?.();
  gridApi?.current?.refreshServerSide?.({ purge: true });
  queryClient.invalidateQueries({ queryKey: rowViewKeys.all(datasetId) });
  queryClient.invalidateQueries({ queryKey: ["dataset-detail", datasetId] });
}

/**
 * integration · `Restore n rows…` (DV-06): rows deleted after a version come back
 * with their own ids and row numbers, as they were in that version (N2
 * `restore_rows`), saved as a new version. Opened from the as-of selection
 * bar, the Row view's deleted-row banner and ⌘K. Blocked, like Restore
 * version, while unsaved changes are pending.
 */
const RestoreRowsDialog = ({ datasetId }) => {
  const request = useRestoreRowsDialog((s) => s.request);
  const close = useRestoreRowsDialog((s) => s.close);
  const { latest } = useAsOfVersion(datasetId);
  const { viewVersion } = useVersionUrl();
  const cs = useChangeSet(datasetId);
  const save = useSaveDatasetVersion(datasetId);
  const queryClient = useQueryClient();
  const { gridApi } = useDevelopDetailContext();
  if (!request || !latest) return null;
  const text = restoreRowsText({
    rows: request.rows.map((r) => r.n),
    from: request.from,
    latest: latest.number,
  });
  const blocked = Boolean(cs.hasPending);

  const onConfirm = () =>
    save.mutate(
      {
        base_version_id: latest.id,
        description: text.description,
        operations: [
          {
            op: "restore_rows",
            row_ids: request.rows.map((r) => r.row_id),
            from_version: request.from,
          },
        ],
      },
      {
        onSuccess: (res) => {
          close();
          afterRestore({ queryClient, gridApi, datasetId });
          notify(text.toast(res?.version?.number), {
            key: "rows-restore",
            variant: "success",
            action: () => (
              <Button
                size="small"
                color="inherit"
                data-testid="rows-restore-open-latest"
                onClick={() => viewVersion(null)}
                sx={{ fontWeight: "fontWeightSemiBold", minWidth: 0, px: 1 }}
              >
                Open latest
              </Button>
            ),
          });
        },
        onError: (e) =>
          notify(
            e?.isVersionConflict
              ? "Someone saved a newer version. Reload to restore from it."
              : e?.result ?? e?.message ?? "Couldn't restore the rows",
            { key: "rows-restore", variant: "error" },
          ),
      },
    );

  return (
    <ConfirmDialog
      open
      danger={false}
      busy={save.isPending}
      title={text.title}
      confirmLabel={blocked ? "OK" : text.confirm}
      cancelLabel="Cancel"
      body={
        <Typography
          typography="s1"
          color="text.secondary"
          data-testid="restore-rows-body"
        >
          {blocked ? RESTORE_BLOCKED_PENDING : text.body}
        </Typography>
      }
      onConfirm={blocked ? close : onConfirm}
      onClose={close}
    />
  );
};

RestoreRowsDialog.propTypes = { datasetId: PropTypes.string };

export default RestoreRowsDialog;
