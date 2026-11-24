import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const alertKeys = {
  all: ["errorFeed", "alerts"],
  list: () => [...alertKeys.all, "list"],
  options: () => [...alertKeys.all, "options"],
};

const unwrap = (response) => response?.data?.result ?? response?.data;

// Normalize alert rule fields once at the API boundary so UI views read one shape.
const normalizeAlertRule = (rule) => {
  if (!rule || typeof rule !== "object") return rule;
  const cfg = rule.configuration || {};
  const emailRecipients = Array.isArray(rule.email_recipients)
    ? rule.email_recipients
    : Array.isArray(cfg.email_recipients)
      ? cfg.email_recipients
      : [];
  const slackChannelId = rule.slack_channel_id ?? cfg.slack_channel_id ?? "";
  const slackChannelName =
    rule.slack_channel_name ?? cfg.slack_channel_name ?? "";
  const slackConnectionId =
    rule.slack_connection_id ?? cfg.slack_connection_id ?? "";
  return {
    ...rule,
    slack_enabled:
      rule.slack_enabled ??
      cfg.slack_enabled ??
      Boolean(slackChannelId || slackChannelName),
    slack_connection_id: slackConnectionId,
    slack_channel_id: slackChannelId,
    slack_channel_name: slackChannelName,
    email_enabled:
      rule.email_enabled ?? cfg.email_enabled ?? emailRecipients.length > 0,
    email_recipients: emailRecipients,
  };
};

export const useErrorFeedAlerts = (options = {}) =>
  useQuery({
    queryKey: alertKeys.list(),
    queryFn: () =>
      axios.get(endpoints.errorFeed.alerts.list, {
        params: { kind: "error_feed" },
      }),
    select: (response) => {
      const result = unwrap(response);
      const list =
        result?.alerts ||
        result?.results ||
        (Array.isArray(result) ? result : []);
      return list.map(normalizeAlertRule);
    },
    staleTime: 30_000,
    ...options,
  });

export const useErrorFeedAlertOptions = (options = {}) =>
  useQuery({
    queryKey: alertKeys.options(),
    queryFn: () =>
      axios.get(endpoints.errorFeed.alerts.options, {
        params: { kind: "error_feed" },
      }),
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
        : axios.post(endpoints.errorFeed.alerts.list, {
            ...body,
            kind: "error_feed",
          }),
    onSuccess: () => {
      enqueueSnackbar("Alert rule saved.", { variant: "success" });
      return queryClient.invalidateQueries({ queryKey: alertKeys.all });
    },
    onError: () => {
      if (showErrorToast)
        enqueueSnackbar("Could not save the alert rule.", { variant: "error" });
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
    onError: () =>
      enqueueSnackbar("Could not delete the alert rule.", { variant: "error" }),
  });
};

export const useTestErrorFeedAlert = () =>
  useMutation({
    mutationFn: (id) => axios.post(endpoints.errorFeed.alerts.test(id), {}),
    onSuccess: () =>
      enqueueSnackbar("Test notification sent.", {
        variant: "success",
      }),
    onError: () =>
      enqueueSnackbar("Test notification could not be sent.", {
        variant: "error",
      }),
  });
