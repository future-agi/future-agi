import { useCallback, useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";
import { sniffValue } from "./sniff";
import { COPY_TOAST, copyAs } from "./copyAs";
import {
  FIELD_VIEW_EVENT,
  effectiveMode,
  isHtmlLike,
  modeAvailability,
  readFieldView,
  writeFieldView,
} from "./viewModes";

const RAW_REASON = "Formatted views are off · press R";

/**
 * State of one value's view (ValueViewer / FieldCard): the sniffed value,
 * the mode (per field in `fa.valueView.<fieldKey>`; the global Raw of
 * useValueViewMode wins), wrap and copy-as.
 * @param {{value: *, dataType?: string, fieldKey?: string, basePath?: string,
 *   mode?: "formatted"|"raw", view?: string, onViewChange?: Function,
 *   formSchema?: object}} p  `formSchema` (W3-2 B4): the column's declared
 *   schema; it lists the Form view
 */
export default function useValueView({
  value,
  dataType,
  fieldKey,
  basePath,
  mode = "formatted",
  view: controlled,
  onViewChange,
  formSchema,
}) {
  const form = Boolean(formSchema && typeof formSchema === "object");
  const sniffed = useMemo(
    () => sniffValue(value, { declaredType: dataType, fieldName: fieldKey }),
    [value, dataType, fieldKey],
  );
  const [stored, setStored] = useState(() => readFieldView(fieldKey));
  const [wrap, setWrap] = useState(true);

  useEffect(() => {
    setStored(readFieldView(fieldKey));
    if (!fieldKey) return undefined;
    const sync = (e) => {
      if (!e?.detail || e.detail.fieldKey === fieldKey)
        setStored(readFieldView(fieldKey));
    };
    window.addEventListener(FIELD_VIEW_EVENT, sync);
    return () => window.removeEventListener(FIELD_VIEW_EVENT, sync);
  }, [fieldKey]);

  const baseAvailability = useMemo(
    () => modeAvailability(sniffed, { form }),
    [sniffed, form],
  );
  const raw = mode === "raw";
  const availability = useMemo(
    () =>
      raw
        ? Object.fromEntries(
            Object.entries(baseAvailability).map(([m, r]) => [
              m,
              m === "raw" ? null : r ?? RAW_REASON,
            ]),
          )
        : baseAvailability,
    [raw, baseAvailability],
  );
  const view = raw
    ? "raw"
    : effectiveMode(controlled ?? stored ?? "auto", sniffed, { form });

  const setView = useCallback(
    (m) => {
      if (onViewChange) onViewChange(m);
      if (controlled === undefined) {
        setStored(m === "auto" ? null : m);
        writeFieldView(fieldKey, m);
      }
    },
    [onViewChange, controlled, fieldKey],
  );

  const copy = useCallback(
    (format, path = basePath) => {
      copyToClipboard(copyAs(value, format, { path }));
      enqueueSnackbar(COPY_TOAST[format] ?? "Copied", { variant: "success" });
    },
    [value, basePath],
  );

  return { sniffed, view, setView, availability, wrap, setWrap, copy };
}

/**
 * True when a value is worth a toolbar (json / chat / media / long text,
 * HTML, or a column with a declared schema: `{form: true}`).
 */
export function wantsToolbar(sniffed, value, { form = false } = {}) {
  if (form) return true;
  if (isHtmlLike(value)) return true;
  const k = sniffed?.kind;
  if (!k || k === "empty") return false;
  // Nothing to switch views on for `[]` chats / empty lists.
  if (Array.isArray(sniffed.normalized) && !sniffed.normalized.length)
    return false;
  if (
    [
      "object",
      "array",
      "chat",
      "image",
      "images",
      "audio",
      "video",
      "document",
      "embedding",
      "ref",
      "yaml_text",
    ].includes(k)
  )
    return true;
  return typeof value === "string" && value.length > 400;
}
