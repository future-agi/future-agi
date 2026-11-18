import { format, isValid } from "date-fns";

const REASONS = {
  missing_key: "API key missing",
  missing_assistant_id: "assistant ID missing",
  missing_agent: "no agent is linked to this connection",
  deleted_agent: "the linked agent was deleted",
  credential_unreadable: "the saved API key can't be read, so enter it again",
};

export const describeConfigurationHealth = (health) => {
  if (!health) return null;
  const reason = REASONS[health.reason_code] ?? "the connection is not set up";
  const nextTry = health.next_retry_at ? new Date(health.next_retry_at) : null;
  const when =
    nextTry && isValid(nextTry)
      ? ` Next try at ${format(nextTry, "PPp")}.`
      : "";
  return `Can't import calls: ${reason}.${when}`;
};
