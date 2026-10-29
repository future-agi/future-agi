import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const alertKeys = {
  all: ["errorFeed", "alerts"],
  list: () => [...alertKeys.all, "list"],
  options: () => [...alertKeys.all, "options"],
};

const unwrap = (response) => response?.data?.result ?? response?.data;

export const useErrorFeedAlerts = (options = {}) =>
  useQuery({
    queryKey: alertKeys.list(),
    queryFn: () => axios.get(endpoints.errorFeed.alerts.list, { params: { kind: "error_feed" } }),
    select: (response) => {
      const result = unwrap(response);
      return result?.alerts || result?.results || (Array.isArray(result) ? result : []);
    },
    staleTime: 30_000,
    ...options,
  });

export const useErrorFeedAlertOptions = (options = {}) =>
  useQuery({
    queryKey: alertKeys.options(),
    queryFn: () => axios.get(endpoints.errorFeed.alerts.options, { params: { kind: "error_feed" } }),
    select: unwrap,
    staleTime: 5 * 60_000,
    ...options,
  });

export const useSaveErrorFeedAlert = ({ showErrorToast = true } = {}) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }) =>
      id
        ? axios.patch(endpoints.errorFeed.alerts.detail(id), body)
        : axios.post(endpoints.errorFeed.alerts.list, { ...body, kind: "error_feed" }),
    onSuccess: () => {
      enqueueSnackbar("Alert rule saved.", { variant: "success" });
      return queryClient.invalidateQueries({ queryKey: alertKeys.all });
    },
    onError: () => {
      if (showErrorToast) enqueueSnackbar("Could not save the alert rule.", { variant: "error" });
    },
  });
};

export const useDeleteErrorFeedAlert = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id) => axios.delete(endpoints.errorFeed.alerts.detail(id)),
    onSuccess: () => {
      enqueueSnackbar("Alert rule deleted.", { variant: "success" });
      return queryClient.invalidateQueries({ queryKey: alertKeys.all });
    },
    onError: () => enqueueSnackbar("Could not delete the alert rule.", { variant: "error" }),
  });
};

export const useTestErrorFeedAlert = () =>
  useMutation({
    mutationFn: (id) => axios.post(endpoints.errorFeed.alerts.test(id), {}),
    onSuccess: () => enqueueSnackbar("Test notification sent to Slack.", { variant: "success" }),
    onError: () => enqueueSnackbar("Test notification could not be sent.", { variant: "error" }),
  });
