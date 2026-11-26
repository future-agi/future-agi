import { OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS } from "src/config/runtime_limits";

const truncatePreview = (value) => {
  if (value.length <= OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS) return value;
  return `${value.slice(0, OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS - 1)}…`;
};

const isJsonOmittedValue = (value) =>
  value === undefined ||
  typeof value === "function" ||
  typeof value === "symbol";

/**
 * Build at most one cell preview worth of JSON.
 *
 * Calling JSON.stringify on an arbitrarily large attribute first creates the
 * entire string and only truncates it afterwards. At production payload sizes
 * that temporary allocation is enough to stall or kill the renderer. This
 * writer stops traversing as soon as the preview budget is exhausted.
 */
const boundedJsonPreview = (value) => {
  const captureLimit = OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 1;
  let rendered = "";
  let overflowed = false;
  const seen = new WeakSet();

  const append = (chunk) => {
    if (overflowed || !chunk) return;
    const remaining = captureLimit - rendered.length;
    if (chunk.length > remaining) {
      rendered += chunk.slice(0, Math.max(0, remaining));
      overflowed = true;
      return;
    }
    rendered += chunk;
  };

  const appendQuoted = (text) => {
    const source = String(text);
    const boundedText = source.slice(0, captureLimit);
    append(JSON.stringify(boundedText));
    if (source.length > boundedText.length) overflowed = true;
  };

  const visit = (candidate, depth = 0, inArray = false) => {
    if (overflowed) return;
    if (candidate === null) {
      append("null");
      return;
    }
    if (isJsonOmittedValue(candidate)) {
      if (inArray) append("null");
      return;
    }
    if (typeof candidate === "string") {
      appendQuoted(candidate);
      return;
    }
    if (typeof candidate === "number") {
      append(Number.isFinite(candidate) ? String(candidate) : "null");
      return;
    }
    if (typeof candidate === "boolean") {
      append(candidate ? "true" : "false");
      return;
    }
    if (typeof candidate === "bigint") {
      appendQuoted(candidate.toString());
      return;
    }
    if (typeof candidate !== "object") {
      appendQuoted(String(candidate));
      return;
    }
    if (depth >= 64) {
      appendQuoted("[MaxDepth]");
      return;
    }
    if (seen.has(candidate)) {
      appendQuoted("[Circular]");
      return;
    }

    seen.add(candidate);
    if (Array.isArray(candidate)) {
      append("[");
      for (let index = 0; index < candidate.length && !overflowed; index += 1) {
        if (index > 0) append(",");
        visit(candidate[index], depth + 1, true);
      }
      append("]");
      seen.delete(candidate);
      return;
    }

    append("{");
    let wroteProperty = false;
    try {
      for (const key in candidate) {
        if (overflowed) break;
        if (!Object.prototype.hasOwnProperty.call(candidate, key)) continue;
        let nestedValue;
        try {
          nestedValue = candidate[key];
        } catch {
          nestedValue = "[Unreadable]";
        }
        if (isJsonOmittedValue(nestedValue)) continue;
        if (wroteProperty) append(",");
        appendQuoted(key);
        append(":");
        visit(nestedValue, depth + 1, false);
        wroteProperty = true;
      }
    } catch {
      if (wroteProperty) append(",");
      appendQuoted("[Unserializable]");
    }
    append("}");
    seen.delete(candidate);
  };

  visit(value);
  if (!overflowed && rendered.length <= OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS) {
    return { truncated: false, rendered };
  }
  return {
    truncated: true,
    rendered: `${rendered.slice(0, OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS - 1)}…`,
  };
};

const boundCellValue = (value) => {
  if (typeof value === "string") return truncatePreview(value);
  if (!Array.isArray(value) && (value === null || typeof value !== "object")) {
    return value;
  }
  const preview = boundedJsonPreview(value);
  return preview.truncated ? preview.rendered : value;
};

/**
 * Keep list rows bounded before cursor overflow and AG Grid cache retain them.
 * Full values remain available from the trace/span detail endpoints.
 */
export const boundObserveListRow = (row) => {
  if (!row || typeof row !== "object") return row;
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, boundCellValue(value)]),
  );
};

/**
 * Keep only the parsed payload needed after a list request completes.
 *
 * Axios responses carry the underlying XMLHttpRequest in `request`. Browsers
 * may retain that request's full responseText, so copying the response and
 * merely replacing `data.table` still pins the original unbounded payload in
 * the cursor cache. Return a plain response-shaped object instead.
 */
export const compactObserveListResponse = (response) => {
  const data =
    response?.data && typeof response.data === "object" ? response.data : {};
  const result =
    data.result && typeof data.result === "object"
      ? { ...data.result, table: [] }
      : data.result;

  return {
    data: {
      ...data,
      ...(Array.isArray(data.table) ? { table: [] } : {}),
      ...(result === undefined ? {} : { result }),
    },
  };
};
