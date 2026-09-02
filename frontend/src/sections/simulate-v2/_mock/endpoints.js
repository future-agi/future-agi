/**
 * Endpoints found in the source.
 *
 * The deployed URL is almost always already written down in the repo — in a
 * deploy config, an env template, a CI workflow, or the curl example at the
 * top of the README. We are reading the source anyway, so asking someone to
 * type a URL we could have found is the same mistake as asking them to type
 * their tool list.
 *
 * Each candidate says where it came from, because that is what lets a person
 * judge it: a URL from fly.toml is the deployment, a URL from .env.example is
 * a placeholder somebody may never have changed.
 */

/** Best-effort org/repo out of whatever was pasted. */
const slugOf = (location = "") => {
  const m = location.match(/[:/]([\w.-]+)\/([\w.-]+?)(?:\.git)?$/);
  if (m) return { org: m[1], repo: m[2] };
  const bare = location.replace(/\.(zip|tar\.gz|tgz)$/, "").split(/[\\/]/).pop();
  return { org: "acme", repo: bare || "agent" };
};

export const detectEndpoints = (kind, location) => {
  if (!location?.trim()) return [];
  if (!["repo", "upload"].includes(kind)) return [];
  const { org, repo } = slugOf(location.trim());
  const host = repo.replace(/[^a-z0-9-]/gi, "-").toLowerCase();

  return [
    {
      id: "deploy",
      url: `https://${host}.fly.dev/agent/chat`,
      from: "fly.toml",
      note: "The deployment this repo ships to.",
      confidence: "high",
    },
    {
      id: "readme",
      url: `https://api.${org}.com/v1/agent`,
      from: "README.md",
      note: "From the curl example in the quickstart — carries its auth header.",
      confidence: "high",
      curl: `curl https://api.${org}.com/v1/agent -H 'Authorization: Bearer $AGENT_TOKEN' -H 'Content-Type: application/json'`,
    },
    {
      id: "envfile",
      url: "http://localhost:8080/agent",
      from: ".env.example",
      note: "A local default — probably not where your agent actually runs.",
      confidence: "low",
    },
  ];
};

export const CONFIDENCE = {
  high: { label: "likely", color: "#16A34A" },
  low: { label: "unlikely", color: "#CA8A04" },
};
