import axios, { endpoints } from "src/utils/axios";
import logger from "../../utils/logger";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useDebounce } from "src/hooks/use-debounce";

/**
 * Search + pagination state for a server-paginated, searchable list.
 *
 * Resets to page 1 whenever the debounced search term changes: the filtered
 * result set can have fewer pages than the current page, and requesting a
 * page that no longer exists makes the backend answer "Invalid page." (#1485)
 */
export function useSearchedPagination(delay = 500, pageSize = 10) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, delay);
  const [pagination, setPagination] = useState({ page: 1, pageSize });

  useEffect(() => {
    setPagination((prev) => (prev.page === 1 ? prev : { ...prev, page: 1 }));
  }, [debouncedSearch]);

  return { search, setSearch, debouncedSearch, pagination, setPagination };
}

/**
 * Enum for simulation source types - matches backend RunTest.SourceTypes
 * Used to differentiate which simulations to fetch from /simulate/run-tests/
 */
export const SIMULATION_TYPE = Object.freeze({
  AGENT_DEFINITION: "agent_definition",
  PROMPT: "prompt",
});

export function getVersionedEvalName(
  baseName,
  prevEvals = [],
  templateId,
  groupName,
) {
  try {
    // Filter previous evals that have same templateId and same baseName
    const sameEvalVersions = prevEvals
      .filter(
        (evalItem) =>
          evalItem.templateId === templateId &&
          evalItem.name.startsWith(baseName),
      )
      .map((evalItem) => {
        const match = evalItem.name.match(/_v(\d+)$/);
        return match ? parseInt(match[1], 10) : 0;
      });
    const nextVersion =
      sameEvalVersions.length > 0 ? Math.max(...sameEvalVersions) + 1 : 0;

    let name = baseName;
    if (groupName && nextVersion > 0) {
      name += `_${groupName}`;
    }

    if (nextVersion > 0) {
      name += `_v${nextVersion + 1}`;
    }

    return name;
  } catch (e) {
    logger.error("Versioning error:", e);
    return baseName;
  }
}
// Usage inside formatGroupMembers:
export function formatGroupMembers(groupObj, members, prevEvals = []) {
  try {
    if (!groupObj || !members) return [];
    return members.map((member) => {
      const groupMapping = groupObj?.config?.mapping || {};
      const formattedMapping = {};

      (member?.requiredKeys || []).forEach((key) => {
        formattedMapping[key] = groupMapping[key] || "";
      });

      const hasFutureEvals = member?.tags?.includes("FUTURE_EVALS");

      const newEvalName = getVersionedEvalName(
        member?.name,
        prevEvals,
        member?.evalTemplateId,
        groupObj?.name,
      );

      return {
        id: `${member?.evalTemplateId || ""}_${Date.now()}`,
        evalId: `${member?.evalTemplateId || ""}_${Date.now()}`,
        name: newEvalName || "",
        groupName: groupObj?.name || "",
        evalTemplateName: member?.name || "",
        evalRequiredKeys: member?.requiredKeys || [],
        evalTemplateTags: member?.tags || [],
        description: member?.description || "",
        optionKeys: member?.optionalKeys || [],
        isModelRequired: false,
        type: hasFutureEvals ? "futureagi_built" : "",
        templateId: member?.evalTemplateId || "",
        model: hasFutureEvals ? groupObj?.model || "" : "",
        config: {
          mapping: formattedMapping,
          config: groupObj?.config?.config || {},
          reasonColumn: groupObj?.config?.reasonColumn ?? false,
        },
        errorLocalizer: groupObj?.errorLocalizer ?? false,
        kbId: groupObj?.kbId || "",
        run: groupObj?.run ?? false,
      };
    });
  } catch (e) {
    logger.error("Formatting error:", e);
    return [];
  }
}
// Base eval columns use dot-hierarchy keys matching the backend
// TRANSCRIPT_DOT_ALIASES in simulate/temporal/activities/xl.py. Legacy
// underscore saved configs still resolve via the backward-compat
// `known_keys` branches in _run_single_evaluation.
export const voiceEvalColumns = [
  { field: "call.transcript", headerName: "call.transcript", dataType: "text" },
  {
    field: "call.voice_recording",
    headerName: "call.voice_recording",
    dataType: "audio",
  },
  {
    field: "call.assistant_recording",
    headerName: "call.assistant_recording",
    dataType: "audio",
  },
  {
    field: "call.customer_recording",
    headerName: "call.customer_recording",
    dataType: "audio",
  },
  {
    field: "call.stereo_recording",
    headerName: "call.stereo_recording",
    dataType: "audio",
  },
  {
    field: "call.agent_prompt",
    headerName: "call.agent_prompt",
    dataType: "text",
  },
];

export const useAgentDefinitions = (
  agentType = null,
  agentDefinitionId = null,
) => {
  const {
    data,
    isFetchingNextPage,
    fetchNextPage,
    isPending,
    hasNextPage,
    isLoading,
  } = useInfiniteQuery({
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.agentDefinitions.list, {
        params: {
          page: pageParam,
          limit: 10,
          ...(agentDefinitionId && { agent_definition_id: agentDefinitionId }),
          ...(agentType && { agent_type: agentType }),
        },
      }),
    queryKey: ["agent-definition-list", agentType, agentDefinitionId],
    getNextPageParam: ({ data }) =>
      data?.next ? data?.current_page + 1 : null,
    initialPageParam: 1,
  });

  const agentDefinitions = data?.pages.flatMap((page) => page?.data?.results);

  return {
    agentDefinitions,
    fetchNextPage,
    isFetchingNextPage,
    isPending,
    hasNextPage,
    isLoading,
  };
};

export const chatEvalColumns = [
  {
    field: "call.transcript",
    headerName: "call.transcript",
    dataType: "text",
  },
  {
    field: "call.agent_prompt",
    headerName: "call.agent_prompt",
    dataType: "text",
  },
  {
    field: "call.user_chat_transcript",
    headerName: "call.user_chat_transcript",
    dataType: "text",
  },
  {
    field: "call.assistant_chat_transcript",
    headerName: "call.assistant_chat_transcript",
    dataType: "text",
  },
];
