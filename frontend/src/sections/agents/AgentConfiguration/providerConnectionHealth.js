import { format, isValid } from "date-fns";

const REASONS = {
  missing_key: "API key missing",
  missing_assistant_id: "assistant ID missing",
  missing_agent: "the connection is not linked to this agent",
  deleted_agent: "the connection is not linked to this agent",
  credential_unreadable: "the saved API key can't be read, so enter it again",
};

export const describeConfigurationHealth = (health, now = new Date()) => {
  if (!health) return null;
  const reason = REASONS[health.reason_code] ?? "the connection is not set up";
  const nextTry = health.next_retry_at ? new Date(health.next_retry_at) : null;
  let when = "";
  if (nextTry && isValid(nextTry)) {
    when =
      nextTry > now
        ? ` Next try at ${format(nextTry, "PPp")}.`
        : " Trying again on the next import run.";
  }
  return `Can't import calls: ${reason}.${when}`;
};
