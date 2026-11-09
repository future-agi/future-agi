// React Query hooks for the public reads (SPEC §9.2). Failures are shown by
// the page itself (meta.errorHandled), never by the app's global toast.
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { hubGet, hubGetText } from "./client";
import { hubApi } from "./endpoints";

const quiet = { errorHandled: true };
const noRetryOn4xx = (count, error) =>
  !(error?.status >= 400 && error?.status < 500) && count < 1;

export const hubKeys = {
  all: ["omega-hub"],
  rules: () => ["omega-hub", "rules"],
  me: () => ["omega-hub", "me"],
  entries: (params) => ["omega-hub", "entries", params],
  entry: (p, n) => ["omega-hub", "entry", p, n],
  versions: (p, n) => ["omega-hub", "versions", p, n],
  version: (p, n, v) => ["omega-hub", "version", p, n, v],
  file: (p, n, v, path) => ["omega-hub", "file", p, n, v, path],
  remixes: (p, n) => ["omega-hub", "remixes", p, n],
  picks: () => ["omega-hub", "picks"],
  tags: () => ["omega-hub", "tags"],
  publishers: (params) => ["omega-hub", "publishers", params],
  publisher: (handle) => ["omega-hub", "publisher", handle],
};

export function useHubRules() {
  return useQuery({
    queryKey: hubKeys.rules(),
    queryFn: () => hubGet(hubApi.rules),
    staleTime: 5 * 60 * 1000,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubEntries(params) {
  return useQuery({
    queryKey: hubKeys.entries(params),
    queryFn: () => hubGet(hubApi.entries, params),
    placeholderData: keepPreviousData,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubEntry(publisher, name) {
  return useQuery({
    queryKey: hubKeys.entry(publisher, name),
    queryFn: () => hubGet(hubApi.entry(publisher, name)),
    enabled: Boolean(publisher && name),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubVersions(publisher, name, enabled = true) {
  return useQuery({
    queryKey: hubKeys.versions(publisher, name),
    queryFn: () => hubGet(hubApi.versions(publisher, name)),
    enabled: Boolean(enabled && publisher && name),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubVersion(publisher, name, version) {
  return useQuery({
    queryKey: hubKeys.version(publisher, name, version),
    queryFn: () => hubGet(hubApi.version(publisher, name, version)),
    enabled: Boolean(publisher && name && version),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubVersionFile(publisher, name, version, path) {
  return useQuery({
    queryKey: hubKeys.file(publisher, name, version, path),
    queryFn: () => hubGetText(hubApi.versionFile(publisher, name, version, path)),
    enabled: Boolean(publisher && name && version && path),
    staleTime: Infinity,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubRemixes(publisher, name, enabled = true) {
  return useQuery({
    queryKey: hubKeys.remixes(publisher, name),
    queryFn: () => hubGet(hubApi.remixes(publisher, name)),
    enabled: Boolean(enabled && publisher && name),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubPicks() {
  return useQuery({
    queryKey: hubKeys.picks(),
    queryFn: () => hubGet(hubApi.picks),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubTags() {
  return useQuery({
    queryKey: hubKeys.tags(),
    queryFn: () => hubGet(hubApi.tags),
    staleTime: 5 * 60 * 1000,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubPublishers(params) {
  return useQuery({
    queryKey: hubKeys.publishers(params),
    queryFn: () => hubGet(hubApi.publishers, params),
    placeholderData: keepPreviousData,
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export function useHubPublisher(handle) {
  return useQuery({
    queryKey: hubKeys.publisher(handle),
    queryFn: () => hubGet(hubApi.publisher(handle)),
    enabled: Boolean(handle),
    retry: noRetryOn4xx,
    meta: quiet,
  });
}

export { noRetryOn4xx, quiet };
