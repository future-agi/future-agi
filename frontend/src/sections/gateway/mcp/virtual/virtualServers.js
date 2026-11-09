// Virtual-server configuration and candidate inventory. The raw admin tool
// list does not contain the gateway composer's retained name holders, so it
// cannot establish the served tool set (including refresh quarantine).

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;

export const NAMING = [
  {
    value: "prefixed",
    label: "Prefixed",
    example: "support-tools__create_ticket",
  },
  { value: "flat", label: "Flat", example: "create_ticket" },
];

// The gateway's MCP endpoint for a virtual server (or its default, /mcp,
// with no slug): the OpenAI-compatible base URL without its /v1.
export function mcpEndpoint(baseUrl, slug) {
  if (!baseUrl) return null;
  const root = String(baseUrl).replace(/\/+$/, "").replace(/\/v1$/, "");
  return slug ? `${root}/mcp/v/${slug}` : `${root}/mcp`;
}

// Organization MCP status is independent of a virtual server's candidate
// inventory. Candidates do not prove this endpoint is serving: composition,
// retained-name quarantine and caller access are resolved by the gateway.
export function virtualServerStatus(
  verdict,
  candidateCount,
  { toolsKnown = true } = {},
) {
  const kind = verdict?.kind;
  if (kind === "gateway_off" || kind === "org_off") {
    return { tone: "neutral", label: "MCP off" };
  }
  if (kind === "empty") return { tone: "neutral", label: "No candidates" };
  if (kind !== "on" || !toolsKnown) {
    return { tone: "neutral", label: "Status unknown" };
  }
  if (!candidateCount) return { tone: "neutral", label: "No candidates" };
  return { tone: "healthy", label: "MCP on" };
}

// vserver.globMatch: "*" matches any run of characters, "/" included.
export function globMatch(pattern, value) {
  const parts = String(pattern).split("*");
  if (parts.length === 1) return pattern === value;
  if (!value.startsWith(parts[0])) return false;
  let rest = value.slice(parts[0].length);
  for (const part of parts.slice(1, -1)) {
    const index = rest.indexOf(part);
    if (index < 0) return false;
    rest = rest.slice(index + part.length);
  }
  return rest.endsWith(parts[parts.length - 1]);
}

const anyMatch = (patterns, names) =>
  (patterns || []).some((pattern) =>
    names.some((name) => globMatch(pattern, name)),
  );

function parseMember(id) {
  const at = id.indexOf("@");
  if (at > 0 && at < id.length - 1) {
    return { id, server: id.slice(0, at), account: id.slice(at + 1) };
  }
  return { id, server: id, account: "" };
}

function upstreamName(tool) {
  if (tool.upstream_name) return tool.upstream_name;
  const prefix = `${tool.server_id}__`;
  return tool.name?.startsWith(prefix)
    ? tool.name.slice(prefix.length)
    : tool.name;
}

// Uncomposed candidates after member, include/exclude and hidden-override
// filtering. Keep their upstream references, not guessed exposed names.
// Normalization, reserved names, collisions and incumbent quarantine belong
// to the gateway composer and are deliberately not predicted here.
export function toolCandidates(entry, tools) {
  const out = [];
  const include = entry?.tools?.include;
  const exclude = entry?.tools?.exclude;
  const overrides = entry?.overrides || {};
  (entry?.members || []).forEach((memberId) => {
    const member = parseMember(memberId);
    (tools || [])
      .filter((tool) => tool.server_id === member.server)
      .forEach((tool) => {
        const name = upstreamName(tool);
        const ref = `${member.id}/${name}`;
        const names = member.account
          ? [ref, `${member.server}/${name}`]
          : [ref];
        if (include?.length && !anyMatch(include, names)) return;
        if (anyMatch(exclude, names)) return;
        const override = names.map((n) => overrides[n]).find(Boolean);
        if (override?.hidden) return;
        out.push({ ref, server: member.server });
      });
  });
  return out.sort((a, b) => (a.ref < b.ref ? -1 : a.ref > b.ref ? 1 : 0));
}

export function virtualServerRows(mcp, tools, baseUrl) {
  const servers = mcp?.virtual_servers;
  if (!servers || typeof servers !== "object") return [];
  return Object.entries(servers)
    .filter(([, entry]) => entry && typeof entry === "object")
    .map(([slug, entry]) => ({
      slug,
      id: entry.id || "",
      title: entry.title || slug,
      description: entry.description || "",
      members: Array.isArray(entry.members) ? entry.members : [],
      naming: entry.naming || "prefixed",
      candidateCount: toolCandidates(entry, tools).length,
      endpoint: mcpEndpoint(baseUrl, slug),
      access: entry.access,
      isDefault: entry.access?.default_for_org === true,
      notEnforced: notEnforced(entry),
      entry,
    }))
    .sort((a, b) => a.title.localeCompare(b.title));
}

// Non-dollar limits and the accepted approval kernel are enforced. Dollar
// tool budgets remain stored-only on this release; never label them active.
export function notEnforced(entry) {
  const items = [];
  if (entry?.budgets?.max_tool_usd_per_run != null) {
    items.push({
      key: "budgets",
      label: "Dollar tool budgets",
      detail: "max_tool_usd_per_run",
    });
  }
  return items;
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// vserver.Usable: a virtual server that names no keys, agents or users is
// open to every principal of the org.
export function accessSummary(access) {
  if (access?.default_for_org) return "Every key (organization default)";
  const parts = [];
  if (access?.keys?.length) parts.push(plural(access.keys.length, "key"));
  if (access?.agents?.length) parts.push(plural(access.agents.length, "agent"));
  if (access?.users?.length) parts.push(plural(access.users.length, "user"));
  return parts.length ? parts.join(", ") : "Every key in the organization";
}

export function slugFromTitle(title) {
  return String(title || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .replace(/-+$/, "")
    .slice(0, 63);
}

export function slugError(slug, taken) {
  if (!slug) return "A slug is required.";
  if (!SLUG_PATTERN.test(slug)) {
    return "Use lowercase letters, digits and dashes, starting with a letter or digit (63 at most).";
  }
  if (taken.includes(slug)) return "A virtual server already uses this slug.";
  return "";
}

export function formFromEntry(entry, slug = "") {
  return {
    slug,
    title: entry?.title || "",
    description: entry?.description || "",
    members: Array.isArray(entry?.members) ? [...entry.members] : [],
    naming: entry?.naming || "prefixed",
    include: [...(entry?.tools?.include || [])],
    exclude: [...(entry?.tools?.exclude || [])],
    keys: [...(entry?.access?.keys || [])],
    agents: [...(entry?.access?.agents || [])],
    defaultForOrg: entry?.access?.default_for_org === true,
  };
}

const listOrNull = (list) => (list && list.length ? [...list] : null);

// The update-config merge patch (RFC 7386) for one virtual server. It sends
// only what the form edits; every other stored field (id, approval, budgets,
// overrides, access rules and users, mode) is left out, so the merge keeps
// it. A null clears a field. A new virtual server gets its id from the API.
export function virtualServerPatch(form, stored) {
  const include = listOrNull(form.include);
  const exclude = listOrNull(form.exclude);
  const keys = listOrNull(form.keys);
  const agents = listOrNull(form.agents);
  const defaultForOrg = form.defaultForOrg ? true : null;
  let access = null;
  if (keys || agents || defaultForOrg || stored?.access) {
    access = { keys, agents, default_for_org: defaultForOrg };
  }
  return {
    virtual_servers: {
      [form.slug]: {
        title: form.title.trim() || null,
        description: form.description.trim() || null,
        members: [...form.members],
        naming: form.naming,
        tools: include || exclude ? { include, exclude } : null,
        access,
        ...(form.limitsPatch !== undefined
          ? { budgets: form.limitsPatch }
          : {}),
      },
    },
  };
}

export function deleteVirtualServerPatch(slug) {
  return { virtual_servers: { [slug]: null } };
}
