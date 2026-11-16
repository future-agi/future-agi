/*
  Error Feed alert rules — the same hooks as dev's src/api/errorFeed/alerts.js,
  answered in memory so the prototype's Alert rules page (dev's page, unchanged)
  opens without the alerts backend. Rules live for the session.
*/
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { OBSERVE_PROJECTS } from "src/sections/simulate-v2/_mock/connectAgent";

const alertKeys = {
  all: ["errorFeed", "alerts"],
  list: () => [...alertKeys.all, "list"],
  options: () => [...alertKeys.all, "options"],
};

let rules = [];
let nextId = 1;

export const useErrorFeedAlerts = (options = {}) =>
  useQuery({
    queryKey: alertKeys.list(),
    queryFn: async () => rules.slice(),
    staleTime: 30_000,
    ...options,
  });

export const useErrorFeedAlertOptions = (options = {}) =>
  useQuery({
    queryKey: alertKeys.options(),
    queryFn: async () => ({
      projects: OBSERVE_PROJECTS.map((p) => ({ value: p.id, label: p.name })),
    }),
    staleTime: 5 * 60_000,
    ...options,
  });

export const useSaveErrorFeedAlert = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }) => {
      if (id) {
        rules = rules.map((r) => (r.id === id ? { ...r, ...body } : r));
        return rules.find((r) => r.id === id);
      }
      const rule = {
        id: `alert-${nextId++}`,
        kind: "error_feed",
        is_active: true,
        created_at: new Date().toISOString(),
        ...body,
      };
      rules = [rule, ...rules];
      return rule;
    },
    onSuccess: () => {
      enqueueSnackbar("Alert rule saved.", { variant: "success" });
      return queryClient.invalidateQueries({ queryKey: alertKeys.all });
    },
  });
};

export const useDeleteErrorFeedAlert = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      rules = rules.filter((r) => r.id !== id);
    },
    onSuccess: () => {
      enqueueSnackbar("Alert rule deleted.", { variant: "success" });
      return queryClient.invalidateQueries({ queryKey: alertKeys.all });
    },
  });
};

export const useTestErrorFeedAlert = () =>
  useMutation({
    mutationFn: async () => true,
    onSuccess: () =>
      enqueueSnackbar("Test notification sent to Slack.", {
        variant: "success",
      }),
  });
