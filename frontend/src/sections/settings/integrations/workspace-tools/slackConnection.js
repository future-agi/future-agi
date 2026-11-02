/**
 * The workspace's real Slack connection, from the integration connections list.
 * Slack lives under Code & collaboration, so the observability lists leave it out.
 */
export const SLACK_PLATFORM = "slack";

/** Connected means the backend says it's active; an errored or paused install isn't. */
export const isSlackActive = (connection) => connection?.status === "active";

/** The Slack connection the page shows: an active one first, else any (so a broken one can be re-added). */
export const slackConnectionOf = (connections) => {
  const slack = (Array.isArray(connections) ? connections : []).filter(
    (c) => c?.platform === SLACK_PLATFORM,
  );
  return slack.find(isSlackActive) || slack[0] || null;
};

export const withoutSlack = (items, key = "platform") =>
  (Array.isArray(items) ? items : []).filter(
    (item) => item?.[key] !== SLACK_PLATFORM,
  );

/** Leaves the app for Slack's own authorize page. */
export const openExternal = (url) => window.location.assign(url);

/** Where Slack sends people back to: the integrations page reads this after OAuth. */
export const SLACK_RETURN_TO_KEY = "slack-integration-return-to";

/** The Slack integration page, at org or workspace level. */
export const isSlackPagePath = (path) =>
  typeof path === "string" &&
  /^\/dashboard\/settings\/(workspace\/[^/]+\/)?integrations\/slack\/?$/.test(
    path,
  );
