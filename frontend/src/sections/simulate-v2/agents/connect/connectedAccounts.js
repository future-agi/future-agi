/*
  What you've already connected (competitor pattern — Vercel, Netlify,
  LangSmith, Hamming): the GitHub app's repositories and the hosted-platform
  accounts whose key an agent in Agents already uses, each with the agents
  it can see that aren't in Agents yet. New agent says so on the row it
  belongs to ("Connected · 3 agents ready to add") and opens that row's setup
  with the key or the app's access in place — no extra section to read.
*/
import { agentRecordFromSource } from "../../_mock/agentRegistry";
import { assistantsFor } from "../../_mock/connectAgent";
import {
  gitHostAccountsLabel, gitHostOfUrl, gitHostRepos, githubAccountsLabel, githubInstalls, hostRepoNameOf, installedRepos, scannedAgentSource,
} from "../../_mock/integrations";
import { HOSTED_PLATFORMS_BY_TYPE } from "../../environments/connectOptions";

const platformName = (id) => [...(HOSTED_PLATFORMS_BY_TYPE.voice || []), ...(HOSTED_PLATFORMS_BY_TYPE.chat || [])].find((p) => p.id === id)?.name || id;

/**
 * [{ id, name, logo, label, found, keyRef?, keyFrom?, items: [{ id, name, sub, fresh, open: { kind, initial, id, meta } }] }]
 * — every installed or keyed account, even once all its agents are added
 * (`items` is then empty; `found` counts what it holds in all).
 */
export const connectedAccountsOf = (agents = [], integrations) => {
  const keys = new Set(agents.map((a) => a.key));
  const accounts = [];

  /* GitHub: each repository the app can read, with the agents found in it that aren't connected yet. */
  const all = installedRepos(integrations).map((repo) => {
    const fresh = (repo.agents || []).filter((a) => !keys.has(agentRecordFromSource(scannedAgentSource(repo, a)).key));
    return { repo, fresh };
  });
  const repos = all.filter((r) => r.fresh.length);
  /* Installed is connected — with or without agents left to add. */
  if (githubInstalls(integrations).length) {
    accounts.push({
      id: "github", name: "GitHub", logo: "github", label: githubAccountsLabel(integrations),
      found: all.reduce((sum, r) => sum + (r.repo.agents || []).length, 0),
      items: repos.map(({ repo, fresh }) => ({
        id: `repo:${repo.name}`,
        name: repo.name,
        fresh: fresh.length,
        sub: fresh.length === 1
          ? `${fresh[0].name} · ${repo.language || "code"}`
          : `${fresh.length} agents to connect · ${repo.language || "code"}`,
        icon: "solar:folder-with-files-linear",
        open: {
          kind: "repo",
          /* One agent left in it: chosen already, so Connect is the next click. */
          initial: { host: "github", repo: repo.name, ...(fresh.length === 1 && { agentId: fresh[0].id }) },
          id: "github",
          meta: { name: "GitHub", logo: "github" },
        },
      })),
    });
  }

  /* Hosted platforms: an account is connected when an agent already holds its key. */
  const byProvider = new Map();
  agents.forEach((a) => {
    const v = a.values || {};
    if (!v.provider || !v.apiKeyRef || !(HOSTED_PLATFORMS_BY_TYPE.voice || []).concat(HOSTED_PLATFORMS_BY_TYPE.chat || []).some((p) => p.id === v.provider)) return;
    const have = byProvider.get(v.provider) || { keyRef: v.apiKeyRef, from: a.name, ids: new Set() };
    if (v.agentId) have.ids.add(v.agentId);
    byProvider.set(v.provider, have);
  });
  byProvider.forEach(({ keyRef, from, ids }, provider) => {
    const fresh = assistantsFor(provider).filter((x) => !ids.has(x.id));
    /* The key is held either way, so the account stays — even with nothing left to add. */
    accounts.push({
      id: provider, name: platformName(provider), logo: provider, label: `key from ${from}`,
      found: assistantsFor(provider).length, keyRef, keyFrom: from,
      items: fresh.map((x) => {
        const chatOnly = !x.phone && (x.channels || []).includes("chat");
        return {
          id: `${provider}:${x.id}`,
          name: x.name,
          sub: [chatOnly ? "Chat" : "Voice", x.phone, x.updated && `updated ${x.updated}`].filter(Boolean).join(" · "),
          logo: provider,
          open: {
            kind: "hosted",
            initial: { platform: provider, agentType: chatOnly ? "chat" : "voice", agentId: x.id, name: x.name, apiKeyRef: keyRef, keyFrom: from },
            id: provider,
            meta: { name: platformName(provider), logo: provider },
          },
        };
      }),
    });
  });
  return accounts;
};

/**
 * The catalogue rows whose account is connected, by row id:
 * { line, initial } — `line` replaces the row's next step; `initial` is
 * merged into the setup it opens (the key's reference, where it came from).
 */
export const readyRowsOf = (agents = [], integrations, groups = []) => {
  const accounts = connectedAccountsOf(agents, integrations);
  const rows = groups.flatMap((g) => g.items);
  const out = {};
  /* Everything it holds already in Agents: say so, rather than asking to connect again. */
  const plural = (n, found) => (!n && found ? "All agents added" : `${n} ${n === 1 ? "agent" : "agents"} ready to add`);
  const gh = accounts.find((a) => a.id === "github");
  if (gh) {
    const n = gh.items.reduce((sum, it) => sum + it.fresh, 0);
    /* Installed already: the row adds an agent from it, not a connection. */
    out.github = { line: `Connected: ${gh.label} · ${plural(n, gh.found)}`, action: "Add agent" };
  }
  /* GitLab and Bitbucket: connected groups or workspaces read the same way as GitHub. */
  ["gitlab", "bitbucket"].forEach((host) => {
    const label = gitHostAccountsLabel(host, integrations);
    if (!label) return;
    const have = new Set(agents.map((a) => {
      const url = a.definition?.repoUrl || a.values?.repoUrl || "";
      return gitHostOfUrl(url) === host ? `${hostRepoNameOf(url).toLowerCase()}#${String(a.definition?.entry || a.values?.entry || "").split(":")[0]}` : null;
    }).filter(Boolean));
    const found = gitHostRepos(host, integrations).flatMap((r) => (r.agents || []).map((x) => `${r.name.toLowerCase()}#${x.entry.split(":")[0]}`));
    out[host] = { line: `Connected: ${label} · ${plural(found.filter((k) => !have.has(k)).length, found.length)}`, action: "Add agent" };
  });
  accounts.filter((a) => a.id !== "github").forEach((acc) => {
    rows.filter((r) => r.kind === "hosted" && r.initial?.platform === acc.id).forEach((r) => {
      /* Counted the way its setup lists them, so the row and the list agree. */
      const type = r.initial?.agentType || null;
      const fresh = new Set(acc.items.map((it) => it.id));
      const all = assistantsFor(acc.id, type);
      const n = all.filter((x) => fresh.has(`${acc.id}:${x.id}`)).length;
      /* The key comes along even with nothing left to add — it's never asked for twice. */
      out[r.id] = { line: `Connected · ${plural(n, all.length)}`, action: "Add agent", initial: { apiKeyRef: acc.keyRef, keyFrom: acc.keyFrom } };
    });
  });
  return out;
};
