// Mirrors futureagi/omega_hub/services/refs.py and omega_hub/semver.py.
// The handoff requires a pin, not the server's optional-version form.
const PART = "(?:0|[1-9][0-9]{0,5})";
const PIN = new RegExp(
  `^([a-z][a-z0-9-]{1,38})/([a-z][a-z0-9-]{2,39})@(${PART}\\.${PART}\\.${PART})(?![\\s\\S])`,
);

export function parsePinnedHubRef(ref) {
  const match = typeof ref === "string" && ref.length <= 101 && PIN.exec(ref);
  if (!match)
    throw new Error(
      "Choose an exact Hub publisher/name@version, or upload a project manually.",
    );
  return { ref, publisher: match[1], name: match[2], version: match[3] };
}

export function parseHubSource(params) {
  if (!params.has("hub")) return null;
  if (params.getAll("hub").length !== 1 || params.has("deployment")) {
    throw new Error(
      "Choose one Hub source or one deployment revision, not both. You can upload a project manually instead.",
    );
  }
  return parsePinnedHubRef(params.get("hub"));
}
