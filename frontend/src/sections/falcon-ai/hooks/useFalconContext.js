import { useLocation } from "react-router-dom";
import { useMemo } from "react";
import useFalconStore from "../store/useFalconStore";

// Maps URL prefixes to context domains
const PAGE_CONTEXT_MAP = [
  { prefix: "/dashboard/data/", page: "datasets", entity: "dataset" },
  { prefix: "/dashboard/data", page: "datasets" },
  { prefix: "/dashboard/develop/", page: "datasets", entity: "dataset" },
  { prefix: "/dashboard/develop", page: "datasets" },
  { prefix: "/dashboard/workbench/", page: "prompts", entity: "prompt" },
  { prefix: "/dashboard/workbench", page: "prompts" },
  {
    prefix: "/dashboard/evaluations/",
    page: "evaluations",
    entity: "evaluation",
  },
  { prefix: "/dashboard/evaluations", page: "evaluations" },
  { prefix: "/dashboard/observe", page: "tracing" },
  {
    prefix: "/dashboard/experiments/",
    page: "experiments",
    entity: "experiment",
  },
  { prefix: "/dashboard/experiments", page: "experiments" },
  {
    prefix: "/dashboard/simulate/agent-definitions/",
    page: "agents",
    entity: "agent",
  },
  {
    prefix: "/dashboard/simulate/scenarios/",
    page: "agents",
    entity: "scenario",
  },
  { prefix: "/dashboard/simulate/test/", page: "agents", entity: "test_run" },
  { prefix: "/dashboard/simulate", page: "agents" },
  {
    prefix: "/dashboard/knowledge/",
    page: "datasets",
    entity: "knowledge_base",
  },
  { prefix: "/dashboard/knowledge", page: "datasets" },
  { prefix: "/dashboard/annotations", page: "evaluations" },
  { prefix: "/dashboard/alerts", page: "tracing" },
  { prefix: "/dashboard/users", page: "admin" },
  { prefix: "/dashboard/keys", page: "admin" },
  { prefix: "/dashboard/settings", page: "settings" },
  { prefix: "/dashboard/gateway", page: "gateway" },
  { prefix: "/dashboard/feed", page: "tracing" },
  { prefix: "/dashboard/tasks", page: "tracing" },
];

/**
 * Extracts entity ID from URL path.
 * E.g., "/dashboard/data/abc-123" → "abc-123"
 *        "/dashboard/data/abc-123/rows" → "abc-123"
 */
function extractEntityId(pathname, prefix) {
  const rest = pathname.slice(prefix.length);
  if (!rest) return null;
  // Take the first path segment after the prefix
  const segment = rest.split("/")[0];
  // Validate it looks like a UUID or ID (not a sub-route keyword)
  const keywords = ["list", "create", "new", "edit", "settings", "config"];
  if (keywords.includes(segment.toLowerCase())) return null;
  return segment || null;
}

function buildObserveContext(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  const observeIndex = parts.indexOf("observe");
  if (observeIndex === -1) return null;

  const projectId = parts[observeIndex + 1];
  const detailType = parts[observeIndex + 2];
  const detailId = parts[observeIndex + 3];

  if (detailType === "trace" && detailId) {
    return {
      page: "tracing",
      path: pathname,
      entity_type: "trace",
      entity_id: detailId,
      project_id: projectId || null,
    };
  }

  if (detailType === "voice" && detailId) {
    return {
      page: "tracing",
      path: pathname,
      entity_type: "voice_call",
      entity_id: detailId,
      project_id: projectId || null,
    };
  }

  if (projectId) {
    return {
      page: "tracing",
      path: pathname,
      entity_type: "project",
      entity_id: projectId,
    };
  }

  return { page: "tracing", path: pathname };
}

export function useFalconContext() {
  const { pathname } = useLocation();
  const activePageContext = useFalconStore((s) => s.activePageContext);

  return useMemo(() => {
    if (activePageContext) {
      return {
        page: activePageContext.page || "general",
        path: pathname,
        ...activePageContext,
      };
    }

    const observeContext = buildObserveContext(pathname);
    if (observeContext) return observeContext;

    for (const mapping of PAGE_CONTEXT_MAP) {
      if (pathname.startsWith(mapping.prefix)) {
        const result = {
          page: mapping.page,
          path: pathname,
        };

        // Extract entity ID if this mapping expects one
        if (mapping.entity) {
          const entityId = extractEntityId(pathname, mapping.prefix);
          if (entityId) {
            result.entity_type = mapping.entity;
            result.entity_id = entityId;
          }
        }

        return result;
      }
    }
    return { page: "general", path: pathname };
  }, [pathname, activePageContext]);
}
