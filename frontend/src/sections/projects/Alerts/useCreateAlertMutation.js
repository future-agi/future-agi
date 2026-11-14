import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

// Fields sent with the alert saved event: the alert's kind, frequency and
// notification channel. Webhook URLs, notes, names and filters are not part
// of the event.
export const alertEventFields = (variables = {}, metricType) => {
  const fields = {
    metricType,
    thresholdType: variables?.threshold_type,
    metric_type: variables?.metric_type,
    alert_frequency: variables?.alert_frequency,
    threshold_operator: variables?.threshold_operator,
    notification_method: variables?.slack_webhook_url
      ? "slack"
      : variables?.webhook_url
        ? "webhook"
        : "email",
    notification_email_count: Array.isArray(variables?.notification_emails)
      ? variables.notification_emails.length
      : 0,
  };
  return Object.fromEntries(
    Object.entries(fields).filter(([, value]) => value !== undefined),
  );
};

export const useCreateAlertMutation = ({
  metricType,
  onSuccessCallback,
  reset,
  handleClose,
}) => {
  return useMutation({
    mutationFn: (data) => axios.post(endpoints.project.createMonitor, data),

    onSuccess: (data, variables) => {
      enqueueSnackbar("Alert created successfully", { variant: "success" });

      trackEvent(Events.createAlertConfirmed, {
        [PropertyName.formFields]: alertEventFields(variables, metricType),
      });

      if (typeof handleClose === "function") handleClose();
      if (typeof reset === "function") reset();

      if (typeof onSuccessCallback === "function") {
        onSuccessCallback(data, variables);
      }
    },
  });
};
