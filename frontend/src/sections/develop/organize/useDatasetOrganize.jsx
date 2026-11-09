// The dataset list's organise doors in one hook (the interface contract §5 A1, A6, A7):
// the `Organize ▾` menu for the selection bar, the ⌘K commands (`Set
// metadata…`, `Move to folder…`, `Attach to agents…`) on the selected or
// active datasets, the three dialogs and their toasts (Move carries Undo).
// A host mounts `menu` and `dialogs`; nothing else.
import React, { useCallback, useMemo, useState } from "react";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { notify } from "src/sections/common/ux/toast";
import { sequenceToast } from "src/api/develop/dataset-org";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import OrganizeMenu from "./OrganizeMenu";
import { offeredOrganizeItems } from "./organizeItems";
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
 * @param {{selected: object[], active?: object|null, canEdit: boolean,
 *   folders?: {path, count, total}[]}} args
 *   datasets carry `id`, `name`, `metadata`, `folder`, `agent_ids`
 *   (and `suggested_agents` when the list has them)
 * @returns {{menu: React.ReactNode, dialogs: React.ReactNode,
 *   openFor: (kind: "metadata"|"folder"|"agents", list?: object[]) => void}}
 */
export default function useDatasetOrganize({
  selected,
  active = null,
  canEdit,
  folders = [],
}) {
  const [open, setOpen] = useState(null); // {kind, datasets}
  // L1 (the capability contract): the real backend has no folders, metadata or
  // agent links for datasets, so a real build registers no organise verb
  // and opens no organise dialog (the host renders neither menu nor
  // dialogs there either).
  const enabled = datasetCaps().listOrganize;
  const targets = useMemo(
    () => organizeTargets(selected, active),
    [selected, active],
  );
  const openFor = useCallback(
    (kind, list) => {
      const datasets = list ?? targets;
      if (
        !enabled ||
        !canEdit ||
        !datasets.length ||
        !offeredOrganizeItems().some((item) => item.kind === kind)
      )
        return;
      setOpen({ kind, datasets });
    },
    [enabled, canEdit, targets],
  );
  const close = useCallback(() => setOpen(null), []);

  useRegisterCommands(
    "datasets.organize",
    enabled
      ? offeredOrganizeItems().map((item) => ({
          id: `datasets.organize.${item.kind}`,
          group: "Datasets",
          label: item.command,
          icon: item.icon,
          keywords: ["organize", "dataset", item.kind],
          run: () => openFor(item.kind),
          when: () => canEdit && targets.length > 0,
        }))
      : [],
    [enabled, canEdit, targets, openFor],
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
    menu: <OrganizeMenu onPick={(kind) => openFor(kind)} canEdit={canEdit} />,
    dialogs,
    openFor,
  };
}
