// The standard ⋯ menu of a TraceChip (R2-SPEC §2.1): Open trajectory ·
// Open full trace ↗ · Compare with… · Copy trace id. Items whose action is
// not given are left out; Copy trace id toasts `Trace id copied`.
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";

/**
 * @param {{traceId?: string|null, onOpenTrajectory?: Function,
 *   fullTraceHref?: string|null, onCompare?: Function}} opts
 * @returns {Array<{label: string, icon: string, onClick: Function,
 *   disabled?: boolean}>}
 */
export function traceChipMenu({
  traceId,
  onOpenTrajectory,
  fullTraceHref,
  onCompare,
} = {}) {
  const items = [];
  if (onOpenTrajectory)
    items.push({
      label: "Open trajectory",
      icon: "mdi:transit-connection-variant",
      onClick: onOpenTrajectory,
    });
  if (fullTraceHref)
    items.push({
      label: "Open full trace ↗",
      icon: "mdi:open-in-new",
      onClick: () => window.open(fullTraceHref, "_blank", "noopener"),
    });
  if (onCompare)
    items.push({
      label: "Compare with…",
      icon: "mdi:compare-horizontal",
      onClick: onCompare,
    });
  if (traceId)
    items.push({
      label: "Copy trace id",
      icon: "mdi:content-copy",
      onClick: () => {
        copyToClipboard(traceId);
        enqueueSnackbar("Trace id copied", { variant: "success" });
      },
    });
  return items;
}
