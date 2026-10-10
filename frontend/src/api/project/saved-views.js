import { useRef } from "react";
import { SS_KEY_USER_ID, SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

export const SAVED_VIEWS_KEY = "saved-views";

const FILTER_CONFIG_KEYS = new Set([
  "filters",
  "compare_filters",
  "extra_filters",
  "compare_extra_filters",
]);

const SAVED_VIEW_CONFIG_KEYS = new Set([
  "filters",
  "columns",
  "sort",
  "display",
  "widgets",
  "conversation_id",
  "sub_tab",
  "compare_filters",
  "compare_date_filter",
  "extra_filters",
  "compare_extra_filters",
]);

const CREATE_PAYLOAD_KEYS = new Set([
  "project_id",
  "name",
  "tab_type",
  "visibility",
  "icon",
  "config",
]);

const UPDATE_PAYLOAD_KEYS = new Set(["name", "visibility", "icon", "config", "expected_revision"]);

const mapPayloadKeys = (data, allowedKeys) =>
  Object.fromEntries(
    Object.entries(data || {}).filter(([key, value]) => {
      return allowedKeys.has(key) && value !== undefined;
    }),
  );

export const serializeSavedViewConfig = (config = {}) => {
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("Saved view config must be an object.");
  }

  const unknownKeys = Object.keys(config).filter(
    (key) => !SAVED_VIEW_CONFIG_KEYS.has(key),
  );
  if (unknownKeys.length) {
    throw new Error(
      `Unknown saved view config keys: ${unknownKeys.join(", ")}`,
    );
  }

  return Object.fromEntries(
    Object.entries(config).map(([key, value]) => {
      if (FILTER_CONFIG_KEYS.has(key) && value !== null) {
        if (!Array.isArray(value)) {
          throw new Error(`Saved view config "${key}" must be a filter list.`);
        }
        return [key, serializeFilterListForApi(value)];
      }
      return [key, value];
    }),
  );
};

export const buildCreateSavedViewPayload = (data, forcedTabType) => {
  const payload = mapPayloadKeys(data, CREATE_PAYLOAD_KEYS);
  if (forcedTabType) payload.tab_type = forcedTabType;
  if (payload.config !== undefined) {
    payload.config = serializeSavedViewConfig(payload.config);
  }
  return payload;
};

export const buildUpdateSavedViewPayload = (data) => {
  const payload = mapPayloadKeys(data, UPDATE_PAYLOAD_KEYS);
  if (payload.config !== undefined) {
    payload.config = serializeSavedViewConfig(payload.config);
  }
  return payload;
};

// Names the backend would reject for this user (uniqueness is per created_by,
// case-sensitive). Unknown user id → block nothing and let the server arbitrate,
// rather than over-blocking other users' shared names during auth resolution.
export const getOwnViewNames = (views, currentUserId) =>
  (views ?? [])
    .filter(
      (v) => currentUserId && String(v.created_by?.id) === String(currentUserId),
    )
    .map((v) => v.name);

export const DEFAULT_VIEW_NAME = "Default View";

// UI tab strings ("trace"/"spans") → backend tab_type ("traces"/"spans").
export const tabTypeForSelectedTab = (selectedTab) =>
  selectedTab === "spans" ? "spans" : "traces";

// The current user's own default view for a tab_type, or null. Scoped to
// created_by so "Set default" never adopts (and overwrites) a teammate's shared
// view, and to tab_type so a spans click can't PATCH the traces default with
// spans-shaped config. The name stays "Default View" across tab_types (matching
// existing data) — per-tab defaults would need an is_default flag + migration.
export const findOwnDefaultView = (views, { tabType, userId }) =>
  (views ?? []).find(
    (v) =>
      v?.name === DEFAULT_VIEW_NAME &&
      v?.tab_type === tabType &&
      userId != null &&
      String(v?.created_by?.id) === String(userId),
  ) ?? null;

// Retain the bucket prefix for consumer invalidations, while isolating identities.
const identity = () => [SS_KEY_USER_ID, SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID].map(
  (key) => typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(key),
);
export const savedViewsKey = (projectId, tabType) => {
  const scope = identity();
  const bucket = projectId ? [SAVED_VIEWS_KEY, projectId] : [SAVED_VIEWS_KEY, "workspace", tabType];
  return scope.some(Boolean) ? [...bucket, scope] : bucket;
};
const sameIdentity = (key) => {
  const scope = identity();
  const suffix = Array.isArray(key.at(-1)) ? key.at(-1) : [null, null, null];
  return JSON.stringify(scope) === JSON.stringify(suffix);
};
// Per-client state disappears with the query client. Timestamps never store config.
const consistencyState = new WeakMap();
const stateFor = (client, key) => {
  if (!consistencyState.has(client)) consistencyState.set(client, new Map());
  const states = consistencyState.get(client);
  const hash = JSON.stringify(key);
  if (!states.has(hash)) states.set(hash, { until: 0, written: new Map(), deleted: new Map() });
  return states.get(hash);
};
export const markStrongRead = (queryClient, key) => {
  stateFor(queryClient, key).until = Date.now() + 30_000;
};

export const classifySavedViewError = (err) => {
  const status = err?.response?.status ?? err?.statusCode;
  const body = err?.response?.data ?? err;
  if (status === 409) return { kind: "conflict", current: body?.result?.current };
  if (status === 428) return { kind: "precondition" };
  if (status === 403) return { kind: "forbidden" };
  if (status === 404) return { kind: "unavailable_record" };
  if (!status || status >= 500) return { kind: "unavailable_transport" };
  return { kind: "validation" };
};

const applyOrder = (result, tabOrder) => {
  if (!result || !tabOrder) return result;
  const views = result.custom_views ?? [];
  const byId = new Map(views.map((view) => [view.id, view]));
  const ordered = (tabOrder.order ?? []).map((id) => byId.get(id)).filter(Boolean);
  const ids = new Set(ordered.map((view) => view.id));
  return { ...result, tab_order: tabOrder, custom_views: [...ordered, ...views.filter((view) => !ids.has(view.id))] };
};

export const mergeSavedViews = (cached, incoming, { primary = false, writtenIds = new Set(), deletedIds = new Set() } = {}) => {
  if (!incoming) throw new Error("Could not load saved views");
  const oldById = new Map((cached?.custom_views ?? []).map((view) => [view.id, view]));
  const views = (incoming.custom_views ?? []).filter((v) => !deletedIds.has(v.id)).map((view) => {
    const old = oldById.get(view.id);
    return (old?.revision ?? 0) > (view.revision ?? 0) ? old : view;
  });
  const ids = new Set(views.map((view) => view.id));
  if (!primary) for (const view of oldById.values()) {
    if (writtenIds.has(view.id) && !ids.has(view.id) && !deletedIds.has(view.id)) views.push(view);
  }
  const order = (cached?.tab_order?.revision ?? 0) > (incoming.tab_order?.revision ?? 0)
    ? cached.tab_order : incoming.tab_order;
  return applyOrder({ ...incoming, custom_views: views }, order);
};

const listSavedViews = async (client, key, params, signal, forcePrimary = false) => {
  const state = stateFor(client, key);
  const primary = forcePrimary || state.until > Date.now();
  const scope = JSON.stringify(identity());
  const response = await axios.get(endpoints.savedViews.list, {
    params: { ...params, ...(primary ? { consistency: "primary" } : {}) },
    ...(signal ? { signal } : {}),
  });
  if (signal?.aborted || scope !== JSON.stringify(identity())) throw new Error("Saved view scope changed");
  const recent = (map) => new Set([...map].filter(([, until]) => until > Date.now()).map(([id]) => id));
  return mergeSavedViews(client.getQueryData(key), response.data?.result, {
    primary, writtenIds: recent(state.written), deletedIds: recent(state.deleted),
  });
};

export const resolveExpectedRevision = async (client, key, id, params = {}) => {
  let record = client.getQueryData(key)?.custom_views?.find((view) => view.id === id);
  if (!record?.revision) {
    record = client.getQueriesData({ queryKey: [SAVED_VIEWS_KEY] })
      .filter(([candidate]) => sameIdentity(candidate))
      .flatMap(([, data]) => data?.custom_views ?? []).find((view) => view.id === id && view.revision);
  }
  if (!record?.revision) {
    const response = await axios.get(endpoints.savedViews.detail(id), {
      params: { ...params, consistency: "primary" },
    });
    record = response.data?.result;
  }
  if (!Number.isInteger(record?.revision) || record.revision < 1) {
    throw { statusCode: 428, message: "Refresh the page and try again." };
  }
  return record.revision;
};

const rememberView = (client, key, view) => {
  if (!view?.id || !sameIdentity(key)) return;
  markStrongRead(client, key);
  stateFor(client, key).written.set(view.id, Date.now() + 30_000);
  client.setQueryData(key, (old) => {
    const current = old ?? { default_tabs: [], custom_views: [], tab_order: { revision: 0, order: [] } };
    const previous = current.custom_views.find((v) => v.id === view.id);
    if ((previous?.revision ?? 0) > (view.revision ?? 0)) return current;
    return { ...current, custom_views: previous
      ? current.custom_views.map((v) => v.id === view.id ? { ...v, ...view } : v)
      : [...current.custom_views, view] };
  });
};
export const removeSavedViewFromCache = (client, id) => {
  for (const [key] of client.getQueriesData({ queryKey: [SAVED_VIEWS_KEY] })) {
    if (key.at(-2) === "detail" && key.at(-1) === id) {
      client.setQueryData(key, null);
      continue;
    }
    stateFor(client, key).deleted.set(id, Date.now() + 30_000);
    client.setQueryData(key, (old) => old && ({ ...old,
      custom_views: (old.custom_views ?? []).filter((v) => v.id !== id),
      ...(old.tab_order ? { tab_order: { ...old.tab_order, order: old.tab_order.order.filter((pk) => pk !== id) } } : {}),
    }));
  }
};
const invalidate = (client, key) => {
  if (!sameIdentity(key)) return;
  markStrongRead(client, key);
  client.invalidateQueries({ queryKey: key });
};

export const useGetSavedViews = (projectId) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId);
  return useQuery({ queryKey: key, queryFn: ({ signal }) => listSavedViews(client, key, { project_id: projectId }, signal), staleTime: 60_000, enabled: !!projectId });
};
export const useGetWorkspaceSavedViews = (tabType) => {
  const client = useQueryClient();
  const key = savedViewsKey(null, tabType);
  return useQuery({ queryKey: key, queryFn: ({ signal }) => listSavedViews(client, key, { tab_type: tabType }, signal), staleTime: 60_000, enabled: !!tabType });
};
export const useRefreshSavedViews = (projectId, tabType) => {
  const client = useQueryClient();
  return () => {
    const key = savedViewsKey(projectId, tabType);
    markStrongRead(client, key);
    return client.invalidateQueries({ queryKey: key });
  };
};

const useCreateView = (projectId, tabType) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId, tabType);
  const uncertain = useRef(null);
  return useMutation({
    mutationFn: async (data) => {
      const payload = buildCreateSavedViewPayload(data, tabType);
      const fingerprint = JSON.stringify([key, payload]);
      try {
        const response = await axios.post(endpoints.savedViews.create, payload);
        uncertain.current = null;
        return response;
      } catch (error) {
        const kind = classifySavedViewError(error).kind;
        if (kind === "validation" && uncertain.current === fingerprint && /already exists/i.test(JSON.stringify(error?.response?.data ?? error))) {
          const result = await listSavedViews(client, key, projectId ? { project_id: projectId } : { tab_type: tabType }, undefined, true);
          const record = result.custom_views.find((v) => v.is_owner && v.name === payload.name && v.tab_type === payload.tab_type && JSON.stringify(v.config) === JSON.stringify(payload.config ?? {}));
          if (record) {
            uncertain.current = null;
            return { data: { result: record } };
          }
        }
        if (kind === "unavailable_transport") uncertain.current = fingerprint;
        throw error;
      }
    },
    onSuccess: (response) => { rememberView(client, key, response?.data?.result); invalidate(client, key); },
  });
};
export const useCreateSavedView = (projectId) => useCreateView(projectId);
export const useCreateWorkspaceSavedView = (tabType) => useCreateView(null, tabType);

const useUpdateView = (projectId, tabType) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId, tabType);
  const params = projectId ? { project_id: projectId } : {};
  return useMutation({
    mutationFn: async ({ id, ...data }) => {
      const payload = buildUpdateSavedViewPayload(data);
      payload.expected_revision ??= await resolveExpectedRevision(client, key, id, params);
      return axios.put(endpoints.savedViews.update(id), payload, { params });
    },
    onSuccess: (response) => { rememberView(client, key, response?.data?.result); invalidate(client, key); },
  });
};
export const useUpdateSavedView = (projectId) => useUpdateView(projectId);
export const useUpdateWorkspaceSavedView = (tabType) => useUpdateView(null, tabType);

const useDeleteView = (projectId, tabType) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId, tabType);
  const params = projectId ? { project_id: projectId } : {};
  return useMutation({
    mutationFn: async (input) => {
      const id = typeof input === "string" ? input : input.id;
      const expected = input?.expected_revision ?? await resolveExpectedRevision(client, key, id, params);
      return axios.delete(endpoints.savedViews.delete(id), { params: { ...params, expected_revision: expected } });
    },
    onSuccess: (_response, input) => {
      if (!sameIdentity(key)) return;
      removeSavedViewFromCache(client, typeof input === "string" ? input : input.id);
      invalidate(client, key);
    },
  });
};
export const useDeleteSavedView = (projectId) => useDeleteView(projectId);
export const useDeleteWorkspaceSavedView = (tabType) => useDeleteView(null, tabType);

export const useDuplicateSavedView = (projectId) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId);
  return useMutation({
    mutationFn: ({ id, name }) => axios.post(endpoints.savedViews.duplicate(id), { name }, { params: { project_id: projectId } }),
    onSuccess: (response) => { rememberView(client, key, response?.data?.result); invalidate(client, key); },
  });
};

export const useReorderSavedViews = (projectId) => {
  const client = useQueryClient();
  const keyFor = (data) => savedViewsKey(projectId ?? data.project_id, data.tab_type);
  return useMutation({
    mutationFn: async (data) => {
      const key = keyFor(data);
      let revision = data.expected_revision ?? client.getQueryData(key)?.tab_order?.revision;
      if (revision == null) {
        const result = await listSavedViews(client, key, data.project_id ? { project_id: data.project_id } : { tab_type: data.tab_type }, undefined, true);
        revision = result.tab_order.revision;
      }
      return axios.post(endpoints.savedViews.reorder, { ...data, expected_revision: revision });
    },
    onMutate: async (data) => {
      const key = keyFor(data);
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData(key);
      const order = [...data.order].sort((a, b) => a.position - b.position).map((item) => item.id);
      client.setQueryData(key, (old) => applyOrder(old, { ...old?.tab_order, order }));
      return { previous, key };
    },
    onSuccess: (response, data) => {
      const key = keyFor(data);
      if (sameIdentity(key)) client.setQueryData(key, (old) => applyOrder(old, response?.data?.result?.tab_order));
    },
    onError: (err, _vars, context) => {
      if (!context || !sameIdentity(context.key)) return;
      const { current, kind } = classifySavedViewError(err);
      client.setQueryData(context.key, kind === "conflict" && current
        ? applyOrder(context.previous, current) : context.previous);
    },
    onSettled: (_response, _error, data) => invalidate(client, keyFor(data)),
  });
};

// Named links revalidate authorization on the primary before mounting their data surface.
export const useGetSavedView = (projectId, id) => {
  const client = useQueryClient();
  const key = savedViewsKey(projectId);
  return useQuery({
    queryKey: [...key, "detail", id],
    enabled: Boolean(projectId && id),
    retry: false,
    staleTime: 0,
    queryFn: async ({ signal }) => {
      const response = await axios.get(endpoints.savedViews.detail(id), {
        params: { project_id: projectId, consistency: "primary" }, signal,
      });
      if (signal.aborted || !sameIdentity(key)) throw new Error("Saved view scope changed");
      rememberView(client, key, response.data?.result);
      return response.data?.result;
    },
  });
};
