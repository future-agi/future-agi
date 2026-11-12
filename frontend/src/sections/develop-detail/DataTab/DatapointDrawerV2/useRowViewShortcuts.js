import useRowKeyboardNav from "src/sections/common/RowPanel/useRowKeyboardNav";

/**
 * The §4.1 keymap on the dataset Row view (PRODUCT-SPEC §3.4 Keys):
 * J/K and ↓/↑ (item; iterate r3 G3), Esc (full → split → closed), F, R,
 * D, E, ⌘↵, C, A, ?. R3: `1`–`4` tabs, `S` splits, `X` collapse all, `⇧C`
 * copy row id, `⌘S` save version.
 */
export default function useRowViewShortcuts({
  enabled,
  full,
  setFull,
  closeRow,
  goNext,
  goPrev,
  onDiff,
  onRaw,
  onEdit,
  onSubmit,
  onCopyLink,
  onAddToQueue,
  onHelp,
  onTab,
  onSplits,
  onCollapseAll,
  onCopyRowId,
  onSaveVersion,
  onFindField,
}) {
  useRowKeyboardNav({
    enabled,
    surface: "row",
    onNext: goNext,
    onPrev: goPrev,
    onClose: () => (full ? setFull(false) : closeRow()),
    onFull: () => setFull(!full),
    keys: {
      diff: onDiff,
      raw: onRaw,
      edit: onEdit,
      submit: onSubmit,
      copyLink: onCopyLink,
      addToQueue: onAddToQueue,
      help: onHelp,
      ...(onTab && { tab: onTab }),
      ...(onSplits && { splits: onSplits }),
      ...(onCollapseAll && { collapseAll: onCollapseAll }),
      ...(onCopyRowId && { copyRowId: onCopyRowId }),
      ...(onSaveVersion && { saveVersion: onSaveVersion }),
      // `/` focuses the panel's `Find field…` (wins over the page search).
      ...(onFindField && { "/": onFindField }),
    },
  });
}
