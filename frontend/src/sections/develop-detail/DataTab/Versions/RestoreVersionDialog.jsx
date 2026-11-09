import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { create } from "zustand";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { notify } from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import {
  useAsOfVersion,
  useSaveDatasetVersion,
  useVersionChanges,
} from "src/api/develop/dataset-versions";
import { useChangeSet } from "./changeSetStore";
import useVersionUrl from "./useVersionUrl";
import {
  RESTORE_BLOCKED_PENDING,
  restoreAsNewRowsText,
  restoreItems,
} from "./versionText";
import { typeChangeEmptiedText } from "../columnTypeText";

/** Which version `Restore as v7…` is confirming (one host per page). */
export const useRestoreDialog = create((set) => ({
  version: null,
  open: (version) => set({ version }),
  close: () => set({ version: null }),
}));

/**
 * `Restore as v7…` (RV#30): confirms with what changes against the latest
 * version, then saves a new version holding the old version's rows (N2
 * `restore_from`). Opened from a history entry, the as-of banner and ⌘K.
 */
const RestoreVersionDialog = ({ datasetId }) => {
  const version = useRestoreDialog((s) => s.version);
  const close = useRestoreDialog((s) => s.close);
  const { latest } = useAsOfVersion(datasetId);
  const { viewVersion } = useVersionUrl();
  const cs = useChangeSet(datasetId);
  const since = useVersionChanges(datasetId, version?.id, {
    against: "latest",
    enabled: Boolean(version),
  });
  const save = useSaveDatasetVersion(datasetId);
  if (!version || !latest) return null;
  const next = latest.number + 1;
  const counts = since.data?.counts ?? null;
  const items = counts
    ? restoreItems(counts, version.number, since.data?.status_rows ?? [])
    : [];
  const nothing = counts && items.length === 0;
  // integration: rows deleted after the version come back as new rows (N2
  // restore_from); say so, and where to keep a row's id instead.
  const asNewRows =
    counts && !nothing ? restoreAsNewRowsText(counts, version.number) : null;
  const blocked = Boolean(nothing || cs.hasPending);
  // K4F merge (M12): a column type change (Force update) emptied values of
  // this version in every version; the restore cannot bring them back, so
  // the dialog does not claim that nothing leaves the history.
  const emptiedText =
    counts && !nothing
      ? typeChangeEmptiedText(
          since.data?.emptied_by_type_change,
          version.number,
        )
      : null;
  const historyText = emptiedText
    ? `v${latest.number} stays in the history, and experiments keep the version they ran on. ${emptiedText}`
    : `Nothing leaves the history: v${latest.number} stays, and experiments keep the version they ran on.`;

  const onConfirm = () =>
    save.mutate(
      {
        restore_from: version.id,
        base_version_id: latest.id,
        description: `Restored v${version.number}`,
      },
      {
        onSuccess: (res) => {
          close();
          viewVersion(null);
          notify(
            `Saved v${res?.version?.number ?? next} · the rows of v${version.number}`,
            { key: "version-restore", variant: "success" },
          );
        },
        onError: (e) =>
          notify(e?.result ?? e?.message ?? "Couldn't restore the version", {
            key: "version-restore",
            variant: "error",
          }),
      },
    );

  return (
    <ConfirmDialog
      open
      danger={false}
      busy={save.isPending}
      title={`Restore v${version.number} as v${next}?`}
      confirmLabel={blocked ? "OK" : `Restore as v${next}`}
      cancelLabel="Cancel"
      items={items}
      body={
        <Typography typography="s1" color="text.secondary">
          {nothing
            ? `v${version.number} already matches v${latest.number}. There is nothing to restore.`
            : cs.hasPending
              ? RESTORE_BLOCKED_PENDING
              : `v${next} becomes the latest version, with the ${fmtCount(
                  version.row_count,
                  "row",
                )} of v${version.number}. ${historyText}${
                  asNewRows ? "" : ` Compared with v${latest.number}:`
                }`}
          {/* integration review: the new-rows note sits before "Compared with
              vN:", which leads straight into its list. */}
          {asNewRows && !cs.hasPending && !nothing ? (
            <>
              <Typography
                component="span"
                display="block"
                typography="s2"
                color="text.secondary"
                data-testid="restore-version-new-rows"
                sx={{ mt: 1 }}
              >
                {asNewRows}
              </Typography>
              <Typography
                component="span"
                display="block"
                typography="s1"
                color="text.secondary"
                sx={{ mt: 1 }}
              >
                {`Compared with v${latest.number}:`}
              </Typography>
            </>
          ) : null}
        </Typography>
      }
      onConfirm={blocked ? close : onConfirm}
      onClose={close}
    />
  );
};

RestoreVersionDialog.propTypes = { datasetId: PropTypes.string };

export default RestoreVersionDialog;
