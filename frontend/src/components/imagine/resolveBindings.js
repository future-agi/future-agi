/**
 * Data binding resolver for Imagine widgets.
 *
 * Widgets can have either:
 *   - `config` (static, hardcoded data) — rendered as-is
 *   - `dataBinding` (dynamic) — resolved against the current trace data
 *
 * This makes saved views work across any trace.
 */

// ---------------------------------------------------------------------------
// Context builder — flattens trace data into a queryable shape
// ---------------------------------------------------------------------------

// Trace trees wrap each span as { observation_span, children }. Voice and chat
// call detail sends the span objects themselves as a flat list.
function getSpan(entry) {
  if (entry?.observation_span) return entry.observation_span;
  return entry?.id ? entry : {};
}

function flattenSpans(entries, result = []) {
  if (!entries) return result;
  for (const entry of entries) {
    const span = getSpan(entry);
    if (span?.id) result.push(span);
    if (entry.children?.length) flattenSpans(entry.children, result);
  }
  return result;
}

function buildContext(traceData) {
  if (!traceData) return { spans: [], summary: {}, trace: {}, rootSpan: {} };

  const rawSpans =
    traceData.spans ||
    traceData.observation_spans ||
    traceData.observation_spans ||
    [];
  const spans = flattenSpans(rawSpans);
  const summary = traceData.summary || {};
  const trace = traceData.trace || {};
  const rootSpan = spans[0] || {};

  // Shortcut for voice calls — traceData.transcript or traceData.trace.transcript
  const transcript = traceData.transcript || trace.transcript || [];

  return { spans, summary, trace, rootSpan, transcript };
}

// ---------------------------------------------------------------------------
// Path resolver — "summary.totalDurationMs" → value
// ---------------------------------------------------------------------------

function getPath(obj, path) {
  if (!obj || !path) return undefined;
  const parts = path.split(".");
  let current = obj;
  for (const part of parts) {
    if (current == null) return undefined;
    // Support both camelCase and snake_case
    current =
      current[part] ?? current[toSnakeCase(part)] ?? current[toCamelCase(part)];
  }
  return current;
}

function toSnakeCase(str) {
  return str.replace(/([A-Z])/g, "_$1").toLowerCase();
}

function toCamelCase(str) {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function formatValue(value, format) {
  if (value == null) return "—";
  if (!format) return String(value);

  if (format.includes("{value}")) {
    return format.replace("{value}", value);
  }
  if (format === "datetime") {
    try {
      return new Date(value).toLocaleString();
    } catch {
      return String(value);
    }
  }
  if (format.startsWith("boolean:")) {
    const [trueLabel, falseLabel] = format.slice(8).split("|");
    return value ? trueLabel : falseLabel;
  }
  if (format.startsWith("truncate:")) {
    const max = parseInt(format.slice(9), 10) || 80;
    const str = typeof value === "string" ? value : JSON.stringify(value);
    return str.length > max ? str.slice(0, max) + "..." : str;
  }
  return String(value);
}

// ---------------------------------------------------------------------------
// Compute expression evaluator
// Handles: "max(spans.latency_ms)", "sum(spans.total_tokens, observation_type=llm)",
//          "summary.totalDurationMs - max(spans.latency_ms, observation_type=llm)"
// ---------------------------------------------------------------------------

function filterSpans(spans, filterExpr) {
  if (!filterExpr) return spans;
  // Parse "field=value" filter
  const [field, value] = filterExpr.split("=").map((s) => s.trim());
  return spans.filter((s) => {
    const v = getPath(s, field);
    return String(v).toLowerCase() === value.toLowerCase();
  });
}

function evalAggregate(funcName, spans, fieldPath, filterExpr) {
  const filtered = filterSpans(spans, filterExpr);
  const values = filtered.map((s) => {
    const v = getPath(s, fieldPath);
    return typeof v === "number" ? v : parseFloat(v) || 0;
  });
  if (!values.length) return 0;
  switch (funcName) {
    case "max":
      return Math.max(...values);
    case "min":
      return Math.min(...values);
    case "sum":
      return values.reduce((a, b) => a + b, 0);
    case "avg":
      return values.reduce((a, b) => a + b, 0) / values.length;
    case "count":
      return values.length;
    default:
      return 0;
  }
}

function evaluateCompute(expr, ctx) {
  if (!expr) return undefined;

  // Replace aggregate functions: max(spans.field, filter) → number
  let noMatchingSpans = false;
  let resolved = expr.replace(
    /(max|min|sum|avg|count)\(spans\.(\w+)(?:\s*,\s*([^)]+))?\)/g,
    (_, func, field, filter) => {
      if (!filterSpans(ctx.spans, filter).length) noMatchingSpans = true;
      return String(evalAggregate(func, ctx.spans, field, filter));
    },
  );
  // An aggregate over no spans has no value: show "—" (formatValue), not a
  // made-up 0 such as "0ms" or "0 turns".
  if (noMatchingSpans) return undefined;

  // Replace path references: summary.totalDurationMs → number
  resolved = resolved.replace(/([a-zA-Z][\w.]+)/g, (match) => {
    // Skip if it's already a number
    if (/^\d+(\.\d+)?$/.test(match)) return match;
    const val = getPath(ctx, match);
    return val != null ? String(val) : match;
  });

  // Evaluate simple arithmetic (only +, -, *, /)
  try {
    if (/^[\d\s+\-*/.()]+$/.test(resolved)) {
      // Safe arithmetic eval without Function constructor
      // Split into tokens and compute left-to-right
      const num = resolved
        .replace(/\s/g, "")
        .split(/([+\-*/])/)
        .filter(Boolean)
        .reduce((acc, token, i, arr) => {
          if (i === 0) return parseFloat(token);
          const op = arr[i - 1];
          const val = parseFloat(token);
          if (isNaN(val)) return acc;
          if (op === "+") return acc + val;
          if (op === "-") return acc - val;
          if (op === "*") return acc * val;
          if (op === "/") return val !== 0 ? acc / val : acc;
          return acc;
        }, 0);
      if (!isNaN(num)) return Math.round(num * 100) / 100; // round to 2 decimals
    }
  } catch {
    /* ignore */
  }

  // If not arithmetic, return as string
  return isNaN(Number(resolved)) ? resolved : Number(resolved);
}

// ---------------------------------------------------------------------------
// Widget-specific resolvers
// ---------------------------------------------------------------------------

function resolveChartBinding(binding, ctx) {
  const { seriesFromSpans, seriesFrom, categoryPath, labelFormat } = binding;

  // Determine the source array: spans (default) or a named context path (e.g. "transcript")
  let sourceItems;
  if (seriesFromSpans) {
    sourceItems = ctx.spans;
  } else if (seriesFrom) {
    const resolved = getPath(ctx, seriesFrom);
    sourceItems = Array.isArray(resolved) ? resolved : [];
  }

  if (!sourceItems) {
    // Passthrough: binding already has resolved series/categories (e.g. from Falcon)
    if (binding.series)
      return { series: binding.series, categories: binding.categories || [] };
    return {};
  }

  const seriesConfigs = Array.isArray(seriesFromSpans || binding.seriesConfig)
    ? seriesFromSpans || binding.seriesConfig
    : [seriesFromSpans || binding.seriesConfig].filter(Boolean);
  const series = seriesConfigs.map((s) => ({
    name: s.name || "Value",
    data: sourceItems.map((item) => {
      const val = getPath(item, s.valuePath);
      return typeof val === "number" ? val : parseFloat(val) || 0;
    }),
  }));

  const categories = sourceItems.map((item) => {
    if (labelFormat) {
      return labelFormat.replace(
        /\{(\w+)\}/g,
        (_, key) => getPath(item, key) ?? key,
      );
    }
    return getPath(item, categoryPath || "name") || "unknown";
  });

  return { series, categories, ...(binding.stacked && { stacked: true }) };
}

function resolvePieBinding(binding, ctx) {
  const { groupBy, groupFrom, aggregate } = binding;
  if (!groupBy) return {};

  // Determine source items: spans (default) or a named context path
  let sourceItems;
  if (groupFrom) {
    const resolved = getPath(ctx, groupFrom);
    sourceItems = Array.isArray(resolved) ? resolved : [];
  } else {
    sourceItems = ctx.spans;
  }

  const groups = {};
  for (const item of sourceItems) {
    const key = getPath(item, groupBy) || item[groupBy] || "unknown";
    if (!groups[key]) groups[key] = { count: 0, sum: 0 };
    groups[key].count += 1;

    if (aggregate?.startsWith("sum:")) {
      const sumField = aggregate.slice(4);
      const val = getPath(item, sumField);
      groups[key].sum += typeof val === "number" ? val : parseFloat(val) || 0;
    }
  }

  const labels = Object.keys(groups);
  const series = labels.map((key) =>
    aggregate?.startsWith("sum:") ? groups[key].sum : groups[key].count,
  );

  return {
    series,
    labels,
    ...(binding.centerLabel && { centerLabel: binding.centerLabel }),
  };
}

function resolveMetricBinding(binding, ctx) {
  // Support both simple path and computed expressions
  let rawValue;
  if (binding.compute) {
    rawValue = evaluateCompute(binding.compute, ctx);
  } else {
    rawValue = getPath(ctx, binding.valuePath);
  }
  const value = formatValue(rawValue, binding.valueFormat);

  const result = { value };

  if (binding.subtitleCompute) {
    result.subtitle = formatValue(
      evaluateCompute(binding.subtitleCompute, ctx),
      binding.subtitleFormat,
    );
  } else if (binding.subtitlePath) {
    const rawSub = getPath(ctx, binding.subtitlePath);
    result.subtitle = formatValue(rawSub, binding.subtitleFormat);
  }
  if (binding.icon) result.icon = binding.icon;
  if (binding.trendDirection) result.trendDirection = binding.trendDirection;

  return result;
}

function resolveKeyValueBinding(binding, ctx) {
  const items = (binding.items || []).map((item) => {
    const rawValue = getPath(ctx, item.valuePath);
    return {
      key: item.key,
      value: formatValue(rawValue, item.format),
      mono: item.mono,
    };
  });
  return { items };
}

function resolveTableBinding(binding, ctx) {
  const columns = binding.columns || [];

  // Determine the source array: spans (default) or a named context path (e.g. "transcript")
  let sourceItems;
  if (binding.rowsFromSpans) {
    sourceItems = ctx.spans;
  } else if (binding.rowsFrom) {
    const resolved = getPath(ctx, binding.rowsFrom);
    sourceItems = Array.isArray(resolved) ? resolved : [];
  }

  const rows = sourceItems
    ? sourceItems.map((item) => {
        const row = {};
        for (const col of columns) {
          const field = typeof col === "string" ? col : col.field;
          row[field] = getPath(item, field) ?? item[field] ?? "—";
        }
        return row;
      })
    : [];

  return { columns, rows };
}

function resolveScreenshotBinding(binding, ctx) {
  const { screenshotUrlPath, clicksFromSpans, imageWidth, imageHeight } =
    binding;

  // Get screenshot URL from a specific span or trace attribute
  let screenshotUrl = null;
  if (screenshotUrlPath) {
    screenshotUrl = getPath(ctx, screenshotUrlPath);
    // If path points to a span field, check each span for the first screenshot
    if (!screenshotUrl) {
      for (const span of ctx.spans) {
        const url = getPath(span, screenshotUrlPath);
        if (
          url &&
          typeof url === "string" &&
          (url.startsWith("http") || url.startsWith("data:"))
        ) {
          screenshotUrl = url;
          break;
        }
      }
    }
  }

  // Extract clicks from spans
  const clicks = [];
  if (clicksFromSpans) {
    const { xPath, yPath, labelPath, actionPath, filterType } = clicksFromSpans;
    let step = 0;
    for (const span of ctx.spans) {
      // Optionally filter to specific span types (e.g., only "tool" spans)
      if (
        filterType &&
        (span.observation_type || span.observation_type) !== filterType
      )
        continue;

      // Try to extract coordinates from input (CUA stores actions in input)
      let x, y, action, label;

      // Direct path extraction
      if (xPath && yPath) {
        x = getPath(span, xPath);
        y = getPath(span, yPath);
      }

      // Try parsing input JSON for coordinates
      if (x == null || y == null) {
        try {
          const input =
            typeof span.input === "string"
              ? JSON.parse(span.input)
              : span.input;
          if (input) {
            x = x ?? input.x ?? input.coordinate?.[0] ?? input.coordinates?.x;
            y = y ?? input.y ?? input.coordinate?.[1] ?? input.coordinates?.y;
            action = input.action || input.type;
          }
        } catch {
          /* not JSON */
        }
      }

      if (x != null && y != null) {
        step++;
        label = labelPath ? getPath(span, labelPath) : span.name;
        action = action || (actionPath ? getPath(span, actionPath) : undefined);
        clicks.push({
          x: Number(x),
          y: Number(y),
          step,
          label: label || `Step ${step}`,
          action,
          status: span.status,
          id: span.id,
        });
      }
    }
  }

  return {
    screenshotUrl,
    clicks,
    imageWidth: imageWidth || 1920,
    imageHeight: imageHeight || 1080,
  };
}

// ---------------------------------------------------------------------------
// Main resolver
// ---------------------------------------------------------------------------

/**
 * @param {Array} widgets
 * @param {Object} traceData
 * @param {Function} [getAnalysisCache] - (widgetId) => cachedContent or null
 */
export function resolveBindings(widgets, traceData, getAnalysisCache) {
  if (!widgets?.length) return widgets;

  const ctx = buildContext(traceData);

  return widgets.map((widget) => {
    // No binding and no dynamic analysis — return as-is
    if (!widget.dataBinding && !widget.dynamicAnalysis) return widget;

    const resolved = { ...widget, config: { ...(widget.config || {}) } };
    const binding = widget.dataBinding;

    // Handle dynamic analysis — cache is the ONLY source of truth per trace
    if (widget.dynamicAnalysis && widget.type === "markdown") {
      if (getAnalysisCache) {
        const cached = getAnalysisCache(widget.id);
        if (cached) {
          resolved.config = { content: cached };
          return resolved;
        }
      }
      // No cache for this trace — needs analysis run
      resolved._needsAnalysis = true;
      resolved.config = {};
      return resolved;
    }

    if (!binding) return resolved;

    switch (widget.type) {
      case "screenshot_annotated":
        resolved.config = {
          ...resolved.config,
          ...resolveScreenshotBinding(binding, ctx),
        };
        break;
      case "bar_chart":
      case "line_chart":
      case "area_chart":
        resolved.config = {
          ...resolved.config,
          ...resolveChartBinding(binding, ctx),
        };
        break;

      case "pie_chart":
      case "donut_chart":
        resolved.config = {
          ...resolved.config,
          ...resolvePieBinding(binding, ctx),
        };
        break;

      case "metric_card":
        resolved.config = {
          ...resolved.config,
          ...resolveMetricBinding(binding, ctx),
        };
        break;

      case "key_value":
        resolved.config = {
          ...resolved.config,
          ...resolveKeyValueBinding(binding, ctx),
        };
        break;

      case "data_table":
        resolved.config = {
          ...resolved.config,
          ...resolveTableBinding(binding, ctx),
        };
        break;

      case "markdown":
        if (widget.dynamicAnalysis) {
          resolved._needsAnalysis = true;
          resolved.config = { content: "*Analyzing trace...*" };
        }
        break;

      default:
        break;
    }

    return resolved;
  });
}

export { buildContext, getPath, flattenSpans };
