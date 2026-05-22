import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const SHARED_LINKS_KEY = "shared-links";

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Get shared links for a specific resource.
 * @param {string} resourceType - "trace" | "dashboard" | "eval_run" | etc.
 * @param {string} resourceId - UUID of the resource
 */
export const useGetSharedLinks = (resourceType, resourceId) => {
  return useQuery({
    queryKey: [SHARED_LINKS_KEY, resourceType, resourceId],
    queryFn: () =>
      axios.get(endpoints.sharedLinks.list, {
        params: { resource_type: resourceType, resource_id: resourceId },
      }),
    select: (d) => {
      const body = d.data;
      // Handle { status, result: [...] } or raw array or paginated { results: [...] }
      if (Array.isArray(body)) return body;
      if (Array.isArray(body?.result)) return body.result;
      if (Array.isArray(body?.results)) return body.results;
      return [];
    },
    enabled: !!resourceType && !!resourceId,
    staleTime: 30_000,
  });
};

/**
 * Get shared link detail (including ACL list).
 */
export const useGetSharedLinkDetail = (linkId) => {
  return useQuery({
    queryKey: [SHARED_LINKS_KEY, "detail", linkId],
    queryFn: () => axios.get(endpoints.sharedLinks.detail(linkId)),
    select: (d) => d.data?.result || d.data,
    enabled: !!linkId,
  });
};

/**
 * Resolve a share token (public endpoint).
 */
export const useResolveSharedLink = (token) => {
  return useQuery({
    queryKey: [SHARED_LINKS_KEY, "resolve", token],
    queryFn: () => axios.get(endpoints.sharedLinks.resolve(token)),
    select: (d) => d.data,
    enabled: !!token,
    retry: false,
  });
};

/**
 * Resolve live data for one widget of a shared dashboard (public endpoint).
 * The share token authorizes the read — no workspace auth required.
 */
export const useResolveSharedWidgetData = (token, widgetId, enabled = true) => {
  return useQuery({
    queryKey: [SHARED_LINKS_KEY, "widget-data", token, widgetId],
    queryFn: () =>
      axios.get(endpoints.sharedLinks.widgetData(token, widgetId)),
    select: (d) => d.data?.result ?? d.data,
    enabled: !!token && !!widgetId && enabled,
    retry: false,
    staleTime: 60_000,
  });
};

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useCreateSharedLink = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data) => axios.post(endpoints.sharedLinks.create, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: [
          SHARED_LINKS_KEY,
          variables.resource_type,
          variables.resource_id,
        ],
      });
    },
  });
};

export const useUpdateSharedLink = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }) =>
      axios.patch(endpoints.sharedLinks.update(id), data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [SHARED_LINKS_KEY] });
    },
  });
};

export const useDeleteSharedLink = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.sharedLinks.delete(id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [SHARED_LINKS_KEY] });
    },
  });
};

export const useAddSharedLinkAccess = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ linkId, emails }) =>
      axios.post(endpoints.sharedLinks.addAccess(linkId), { emails }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [SHARED_LINKS_KEY] });
    },
  });
};

export const useRemoveSharedLinkAccess = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ linkId, accessId }) =>
      axios.delete(endpoints.sharedLinks.removeAccess(linkId, accessId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [SHARED_LINKS_KEY] });
    },
  });
};
