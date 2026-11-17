// The dataset list's organise doors: selection menu, palette and dialogs.
import React, { useCallback, useMemo, useState } from "react";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { notify } from "src/sections/common/ux/toast";
import { sequenceToast } from "src/api/develop/dataset-org";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import OrganizeMenu from "./OrganizeMenu";
import { offeredOrganizeItems, organizeItemOff } from "./organizeItems";
import MetadataDialog from "./MetadataDialog";
import MoveToFolderDialog from "./MoveToFolderDialog";
import RealMoveToFolderDialog from "./RealMoveToFolderDialog";
import AgentsDialog from "./AgentsDialog";

/** The datasets a door acts on: the selection, else the active row. Pure. */
export function organizeTargets(selected, active) {
  const list = (selected ?? []).filter(Boolean);
  if (list.length) return list;
  return active ? [active] : [];
}

/** Show a dialog's toast; a Move toast offers Undo. */
export function showOrganizeToast(toast) {
  if (!toast?.message) return;
  const handle = notify(toast.message, {
    variant: toast.variant,
    key: "dataset-organize",
    ...(toast.undo ? { undo: { onUndo: () => {} } } : {}),
  });
  if (!toast.undo) return;
  handle.undone.then(async (undone) => {
    if (!undone) return;
    const res = await toast.undo();
    const back = sequenceToast(res, "Moved back");
    notify(back.message, { variant: back.variant, key: "dataset-organize" });
  });
}

/**
 * The host passes the live useFeatureAllowed(DATASET_METADATA) answer. Missing
 * answers fail closed on a real backend, without changing prototype behavior.
 * Datasets carry id, name, metadata, folder and agent_ids.
 */
export default function useDatasetOrganize({
  selected,
  active = null,
  canEdit,
  folders = [],
  metadataCapability,
}) {
  const [open, setOpen] = useState(null);
  const enabled = datasetCaps().listOrganize;
  const targets = useMemo(
    () => organizeTargets(selected, active),
    [selected, active],
  );
  const openFor = useCallback(
    (kind, list) => {
      const datasets = list ?? targets;
      const item = offeredOrganizeItems().find((it) => it.kind === kind);
      if (!enabled || !canEdit || !datasets.length || !item) return;
      const off = organizeItemOff(item, metadataCapability);
      if (off) {
        notify(off, { variant: "info", key: "dataset-organize" });
        return;
      }
      setOpen({ kind, datasets });
    },
    [enabled, canEdit, targets, metadataCapability],
  );
  const close = useCallback(() => setOpen(null), []);

  useRegisterCommands(
    "datasets.organize",
    enabled
      ? offeredOrganizeItems().map((item) => {
          const off = organizeItemOff(item, metadataCapability);
          return {
            id: `datasets.organize.${item.kind}`,
            group: "Datasets",
            label: item.command,
            icon: item.icon,
            keywords: ["organize", "dataset", item.kind],
            ...(off ? { hint: off } : {}),
            run: () => openFor(item.kind),
            when: () => canEdit && targets.length > 0,
          };
        })
      : [],
    [enabled, canEdit, targets, openFor, metadataCapability],
  );

  const props = open
    ? {
        open: true,
        datasets: open.datasets,
        onClose: close,
        onDone: showOrganizeToast,
      }
    : null;
  const dialogs = props ? (
    <>
      {open.kind === "metadata" && <MetadataDialog {...props} />}
      {open.kind === "folder" &&
        (datasetCaps().real ? (
          <RealMoveToFolderDialog {...props} />
        ) : (
          <MoveToFolderDialog {...props} folders={folders} />
        ))}
      {open.kind === "agents" && <AgentsDialog {...props} />}
    </>
  ) : null;

  return {
    menu: (
      <OrganizeMenu
        onPick={(kind) => openFor(kind)}
        canEdit={canEdit}
        metadataCapability={metadataCapability}
      />
    ),
    dialogs,
    openFor,
  };
}
