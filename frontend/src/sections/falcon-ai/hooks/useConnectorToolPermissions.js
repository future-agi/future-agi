import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  resolveEnabledNames,
  toolActionErrorMessage,
} from "../components/connectorTools";
import { falconAIQueryKeys, updateConnectorTools } from "./useFalconAPI";

/**
 * Owns allow/deny for a connector's tools.
 *
 * Writes are keyed by tool name and always derived from `selectedItem`, the
 * only record holding `discovered_tools`/`enabled_tool_names`; a list row would
 * resolve to an empty set and clear every permission.
 *
 * @param {object} params
 * @param {object|null} params.selectedItem   connector detail currently shown
 * @param {Function} params.setSelectedItem   state setter for that record
 * @param {Function} params.setConnectors     state setter for the list rows
 */
export function useConnectorToolPermissions({
  selectedItem,
  setSelectedItem,
  setConnectors,
}) {
  const [toolError, setToolError] = useState(null);
  const queryClient = useQueryClient();

  const applyEnabledToolNames = useCallback(
    async (connectorId, nextNames) => {
      try {
        await updateConnectorTools(connectorId, nextNames);
        setToolError(null);
        queryClient.setQueryData(
          falconAIQueryKeys.connector(connectorId),
          (prev) => (prev ? { ...prev, enabled_tool_names: nextNames } : prev),
        );
        setSelectedItem((prev) =>
          prev?.id === connectorId
            ? { ...prev, enabled_tool_names: nextNames }
            : prev,
        );
        // tool_count is the only tool-derived field the list carries.
        setConnectors((prev) =>
          prev.map((c) =>
            c.id === connectorId ? { ...c, tool_count: nextNames.length } : c,
          ),
        );
      } catch (error) {
        setToolError(
          toolActionErrorMessage(error, "Failed to update tool permissions."),
        );
      }
    },
    [setSelectedItem, setConnectors, queryClient],
  );

  const handleToolToggle = useCallback(
    async (connectorId, tool) => {
      // Permissions are addressed by name; a nameless tool cannot be targeted.
      if (!tool?.name) return;

      const conn = selectedItem?.id === connectorId ? selectedItem : null;
      if (!conn) return;

      const enabled = resolveEnabledNames(conn);
      const next = enabled.includes(tool.name)
        ? enabled.filter((n) => n !== tool.name)
        : [...enabled, tool.name];

      await applyEnabledToolNames(connectorId, next);
    },
    [selectedItem, applyEnabledToolNames],
  );

  // One request for the whole group — firing the single toggle per tool would
  // race, each call computing its next set from the same stale snapshot.
  const handleToolsAllow = useCallback(
    async (connectorId, tools) => {
      const conn = selectedItem?.id === connectorId ? selectedItem : null;
      if (!conn) return;

      const enabled = resolveEnabledNames(conn);
      const names = tools.map((t) => t.name).filter(Boolean);
      const next = [...new Set([...enabled, ...names])];
      if (next.length === enabled.length) return;

      await applyEnabledToolNames(connectorId, next);
    },
    [selectedItem, applyEnabledToolNames],
  );

  return { toolError, setToolError, handleToolToggle, handleToolsAllow };
}
