import { useCallback } from "react";

import {
  LAST_ENABLED_TOOL_MESSAGE,
  resolveEnabledNames,
} from "../components/connectorTools";
import { useToolPermissionWrites } from "./useToolPermissionWrites";

/**
 * Owns allow/deny for a connector's tools, on both surfaces that offer it.
 *
 * Writes are keyed by tool name and derived from the connector *detail*
 * record, the only one carrying `discovered_tools`/`enabled_tool_names`; a list
 * row would resolve to an empty set and clear every permission.
 *
 * Ordering, optimistic display and rollback live in useToolPermissionWrites.
 *
 * @param {object} params
 * @param {object|null} params.connector  connector detail currently shown
 * @param {Function} params.onApply       (connectorId, names) => void, for any
 *                                        copy of the record the caller holds
 *                                        outside the react-query cache
 * @param {Function} [params.onDrained]   called once the writes settle
 */
export function useConnectorToolPermissions({ connector, onApply, onDrained }) {
  const { toolError, setToolError, pendingFor, queueWrite, desiredNames } =
    useToolPermissionWrites({ onApply, onDrained });

  // Only this connector's unsaved names; the writer tracks every connector it
  // has queued work for, which outlives any one selection.
  const pendingNames = pendingFor(connector?.id);

  const handleToolToggle = useCallback(
    (connectorId, toolName) => {
      // Permissions are addressed by name; a nameless tool cannot be targeted.
      if (!toolName) return;
      if (connector?.id !== connectorId) return;

      const baseline = resolveEnabledNames(connector);
      // What the user wants, which may already be ahead of the server.
      const enabled = desiredNames(connectorId, baseline);
      const next = enabled.includes(toolName)
        ? enabled.filter((n) => n !== toolName)
        : [...enabled, toolName];

      // Refuse the write that would empty the list; the tooltip on that
      // toggle says the same thing before the click. See connectorTools.js.
      if (next.length === 0) {
        setToolError(LAST_ENABLED_TOOL_MESSAGE);
        return;
      }

      queueWrite(connectorId, next, [toolName], baseline);
    },
    [connector, desiredNames, queueWrite, setToolError],
  );

  // One request for the whole group rather than one per tool.
  const handleToolsAllow = useCallback(
    (connectorId, toolNames) => {
      if (connector?.id !== connectorId) return;

      const baseline = resolveEnabledNames(connector);
      const enabled = desiredNames(connectorId, baseline);
      const names = toolNames.filter(Boolean);
      const next = [...new Set([...enabled, ...names])];
      if (next.length === enabled.length) return;

      queueWrite(connectorId, next, names, baseline);
    },
    [connector, desiredNames, queueWrite],
  );

  return {
    toolError,
    setToolError,
    pendingNames,
    handleToolToggle,
    handleToolsAllow,
  };
}
