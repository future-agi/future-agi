// Shared context-menu items (ONE-VIEW-SPEC §3.8). A screen's row menu =
// its row actions (often `actionsFor(surface)`) + these. Pure builders; the
// copy ones toast through notify().
import { copyText } from "../CopyButton";
import { notify } from "../toast";

const absolute = (href) => {
  try {
    return new URL(href, window.location.origin).toString();
  } catch {
    return href;
  }
};

const copyAndToast = async (text, message, key) => {
  if (await copyText(text)) notify(message, { variant: "success", key });
  else
    notify("Couldn't copy. Select the text and copy it.", {
      variant: "error",
      key,
    });
};

/** `Copy link` to `href` (absolute). */
export const copyLinkItem = (href, label = "Copy link") => ({
  id: "copyLink",
  label,
  icon: "mdi:link-variant",
  disabled: !href,
  reason: href ? undefined : "No link for this item",
  run: () => copyAndToast(absolute(href), "Link copied", "ctx-copy-link"),
});

/** `Copy <label> id` (the full id). */
export const copyIdItem = (id, label = "") => ({
  id: "copyId",
  label: label ? `Copy ${label} id` : "Copy id",
  icon: "mdi:identifier",
  disabled: !id,
  run: () => copyAndToast(String(id), "Id copied", "ctx-copy-id"),
});

/** `Open side by side` (Task compare). */
export const openSideBySideItem = (href) => ({
  id: "openSideBySide",
  label: "Open side by side",
  icon: "mdi:compare-horizontal",
  shortcut: "openSideBySide",
  href,
  disabled: !href,
  reason: href ? undefined : "Needs two runs",
});

/** `Open in trace ↗` (new tab). */
export const openTraceItem = (href) => ({
  id: "openTrace",
  label: "Open in trace ↗",
  icon: "mdi:chart-timeline-variant",
  href,
  target: "_blank",
  disabled: !href,
  reason: href ? undefined : "No trace for this item",
});

/** `Hide column` for a column of a useColumnLayout layout. */
export const hideColumnItem = (layout, id) => {
  const col = layout?.columns?.find((c) => c.id === id);
  return {
    id: "hideColumn",
    label: "Hide column",
    icon: "mdi:eye-off-outline",
    disabled: !col || col.locked,
    reason: col?.locked ? `${col.label} is always shown` : undefined,
    run: () => layout.hide(id),
  };
};

/**
 * `Make <run> the baseline` (or `<run> is the baseline`, disabled).
 * @param {{label?: string, isBaseline?: boolean}} run
 * @param {(run) => void} onPin
 */
export const pinBaselineItem = (run, onPin) => ({
  id: "pinBaseline",
  label: run?.isBaseline
    ? `${run?.label ?? "This run"} is the baseline`
    : `Make ${run?.label ?? "this run"} the baseline`,
  icon: "mdi:pin-outline",
  shortcut: "makeBaseline",
  disabled: Boolean(run?.isBaseline) || !onPin,
  run: () => onPin?.(run),
});
