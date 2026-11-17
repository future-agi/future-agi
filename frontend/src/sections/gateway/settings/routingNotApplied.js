// The routing settings this gateway stores but does not run, as read-only rows
// with the enforcement ids they fall under (RoutingConfigTab's read-only
// block). Only what is stored becomes a row.
//
// - Race mode (routing.fastest): the gateway races no request (its hedge
//   plan is empty), so a stored race setting is kept and not applied.
// - The default model and conditional routes: stored, and not applied.
// - Scheduled completions, access groups and traffic mirroring
//   (agentcc/services/routing_key_rules.py UNSUPPORTED_KEYS): no runtime,
//   and a new or changed value is refused when saved.
//
// Strategy, adaptive routing, provider failover, retry, the circuit breaker,
// complexity routing, provider locking and model timeouts run for the org's
// keys and stay editable on the tab.

import { isStored, onOff } from "./ui/storedValues";

const read = (config, key, alias) =>
  config[key] ?? (alias ? config[alias] : undefined);

const routeText = (route) =>
  [route.field, route.operator, route.value].filter(Boolean).join(" ") +
  (route.target ? ` → ${route.target}` : "");

function mirrorText(rule) {
  const target = [rule.target_provider, rule.target_model]
    .filter(Boolean)
    .join("/");
  const rate =
    rule.sample_rate !== undefined && rule.sample_rate !== null
      ? `, ${Math.round(Number(rule.sample_rate) * 100)}% sampled`
      : "";
  return `${rule.source_model || "any model"} → ${target || "no target"}${rate}`;
}

function raceText(fastest) {
  const parts = [onOff(fastest.enabled)];
  const max = fastest.max_concurrent ?? fastest.maxConcurrent;
  if (isStored(max)) parts.push(`up to ${max} at once`);
  const excluded = fastest.excluded_providers ?? fastest.excludedProviders;
  if (Array.isArray(excluded) && excluded.length) {
    parts.push(`excluding ${excluded.join(", ")}`);
  }
  return parts.join(", ");
}

export function storedRoutingOnly(config = {}) {
  const rows = [];
  const add = (id, row) => rows.push({ ...row, id });

  const defaultModel = read(config, "default_model", "defaultModel");
  if (defaultModel) {
    add("routing.defaultModel", {
      label: "Default model",
      value: defaultModel,
      mono: true,
    });
  }
  const routes = read(config, "conditional_routes", "conditionalRoutes") || [];
  routes.forEach((route, index) => {
    add("routing.conditionalRoutes", {
      key: `route-${index}`,
      label: `Conditional route ${index + 1}`,
      value: routeText(route || {}),
      mono: true,
    });
  });
  if (config.strategy === "fastest") {
    add("routing.fastest", { label: "Strategy", value: "Fastest (race)" });
  }
  if (isStored(config.fastest)) {
    add("routing.fastest", {
      label: "Race mode",
      value: raceText(config.fastest),
    });
  }
  if (isStored(config.scheduled)) {
    add("routing.unsupportedKeys", {
      label: "Scheduled completions",
      value: onOff(config.scheduled.enabled),
    });
  }
  const groups = read(config, "access_groups", "accessGroups") || {};
  Object.entries(groups).forEach(([name, group]) => {
    const models = (group && group.models) || [];
    add("routing.unsupportedKeys", {
      key: `group-${name}`,
      label: `Access group ${name}`,
      value: models.length ? models.join(", ") : "No models",
    });
  });
  const mirror = config.mirror;
  if (isStored(mirror)) {
    const rules = mirror.rules || [];
    add("routing.unsupportedKeys", {
      label: "Traffic mirroring",
      value: onOff(mirror.enabled),
    });
    rules.forEach((rule, index) => {
      add("routing.unsupportedKeys", {
        key: `mirror-${index}`,
        label: `Mirror rule ${index + 1}`,
        value: mirrorText(rule || {}),
        mono: true,
      });
    });
  }

  const ids = [...new Set(rows.map((row) => row.id))];
  return { ids, rows: rows.map(({ id: _id, ...row }) => row) };
}
