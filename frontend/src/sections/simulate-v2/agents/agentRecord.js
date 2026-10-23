/*
  The agent record as the Definition tab reads it (RSI PRD, "What Agents
  stores for every agent"): Identity, Source, Reach, Configuration, Links.
  Read-only — for code, a pointer to the commit and the snapshot of what we
  found there, never a copy of the repository.
*/
import { definitionOf, sourceKindOf } from "../_mock/agentRegistry";
import { codeRefOf } from "../_mock/codeRefs";
import { uploadDateOf, uploadNoteOf } from "../_mock/agentUpload";
import { CHANNEL_LABELS, agentChannelsOf } from "../_mock/agentLifecycle";
import { githubAccessLabel, githubAccessOf } from "../_mock/integrations";
import { DIRECTIONS, codeRepoOf, contactNumbersOf, fagiPromptsOf, identityOf, languagesLabel, phoneLabelOf, reachOf, turnLimitLabel } from "./agentProfile";
import { areaValueOf } from "./agentDefinitionSummary";
import { configAreasFor } from "./configAreas";

const CODE_KINDS = ["repo", "upload", "framework"];
export const isCodeAgent = (agent) => CODE_KINDS.includes(sourceKindOf(agent));

/**
 * What kind of agent it is, as the Agents list's Type column says it: Code
 * for an agent read from code (a repository, an upload or an SDK adapter),
 * else the channels it serves — "Voice", "Chat", or "Voice · Chat".
 */
export const agentTypeLabelOf = (agent) => (isCodeAgent(agent)
  ? "Code"
  : agentChannelsOf(agent).map((c) => CHANNEL_LABELS[c] || c).join(" · "));

/** Identity rows: name, owner, type, channels, languages, description. */
export const identityRowsOf = (agent) => {
  const id = identityOf(agent);
  const channels = agentChannelsOf(agent).map((c) => CHANNEL_LABELS[c] || c);
  return [
    { label: "Name", value: agent.name },
    { label: "Owner", value: id.owner },
    { label: "Type", value: channels.join(" and ") },
    { label: "Languages", value: languagesLabel(id.languages, 6) },
    { label: "Description", value: id.description || "None", muted: !id.description },
  ];
};

/**
 * The commit a code version points at: what the version recorded, else the
 * pointer read when it was saved. Never the repository's contents.
 */
export const commitPointerOf = (agent, version) => {
  if (version?.commit?.sha) return { sha: version.commit.sha, message: version.commit.message || null, author: version.commit.author || null };
  /* One hash per version, the same every screen shows (codeRefs). */
  const r = codeRefOf(agent, version);
  return { sha: r?.sha || "0000000", message: r?.message || null, author: r?.author || null };
};

/** Source rows: where it lives, what we read from, credentials, write access. */
export const sourceRowsOf = ({ agent, source, definition, version }) => {
  const kind = sourceKindOf(agent);
  const d = definitionOf(agent);
  const v = agent.values || {};
  const rows = [{ label: "Lives in", value: source.label }];
  if (CODE_KINDS.includes(kind)) {
    const repo = codeRepoOf(agent);
    if (kind === "upload") {
      /* The bundle this version was read from — the newest upload at or before it. */
      const at = (agent.versions || []).findIndex((x) => x.label === version?.label);
      const own = [...(agent.versions || []).slice(0, at < 0 ? undefined : at + 1)].reverse().find((x) => x.bundle);
      rows.push({ label: "Upload", value: own?.bundle || d.bundle || agent.location || "Folder or archive", mono: true });
    } else if (repo) {
      rows.push({ label: "Repository", value: repo.replace(/^https?:\/\//, ""), mono: true });
      rows.push({ label: "Branch", value: d.ref || definition?.ref || "main", mono: true });
      rows.push({ label: "Folder", value: agent.definition?.folder || v.folder || "Repository root", mono: true });
    }
    if (kind === "framework" && d.entry) rows.push({ label: "Entry point", value: d.entry, mono: true });
    /* An upload has no commit — its version is the upload it was read from. */
    if (kind === "upload") {
      rows.push({
        label: "Version",
        value: version ? `${version.label} · ${version.origin === "upload" ? uploadNoteOf(version) : `Uploaded ${uploadDateOf(version.createdAt || version.connectedAt)}`}` : "—",
        mono: true,
        hint: "Each new folder or .zip is saved as the next version — upload one from the top of this page.",
      });
    } else {
      const c = commitPointerOf(agent, version);
      rows.push({
        label: "Commit",
        value: c.message ? `${c.sha} · ${c.message}` : c.sha,
        mono: true,
        hint: "Git stays the source of truth — we keep a pointer to the commit and a snapshot of what we found, not a copy of the repository.",
      });
    }
    if (repo) {
      const access = githubAccessOf(repo);
      rows.push({ label: "Write access", value: access ? githubAccessLabel(access) : "None — the GitHub App can't see this repository" });
    }
    /* One row for what the sandbox runs the code with: the test
       credentials given at connect, or the promise when none were. */
    const envVals = v.envValues || [];
    const secrets = envVals.filter((e) => e.secret).length;
    const files = (v.credentialFiles || []).length;
    const given = [
      envVals.length ? `${envVals.map((e) => e.name).join(", ")}${secrets ? ` · ${secrets} by reference` : ""}` : null,
      files ? `${files} credential file${files === 1 ? "" : "s"}` : null,
    ].filter(Boolean);
    rows.push({
      label: "Test credentials",
      value: given.length ? given.join(" · ") : "Test credentials you provide · production secrets are never read",
      hint: "What our sandbox runs the code with — test credentials only. Secrets are references into your secret manager, never stored here.",
    });
  } else if (kind === "platform") {
    if (v.agentId) rows.push({ label: "Agent ID", value: v.agentId, mono: true });
    rows.push({
      label: "API key",
      value: v.apiKeyRef || (v.hasApiKey ? "Held by the connection · no reference recorded" : "Not stored"),
      mono: !!v.apiKeyRef,
      muted: !v.apiKeyRef && !v.hasApiKey,
    });
  } else if (kind === "traces") {
    rows.push({ label: "Traces from", value: `${d.project || "Observe project"}${v.fromTraces?.traces ? ` · ${Number(v.fromTraces.traces).toLocaleString()} traces read` : ""}`, mono: !!d.project });
    rows.push({ label: "Where it runs", value: "Not known yet — connect it there to test it live or sealed", muted: true });
  } else if (source.runtime?.where) {
    rows.push({ label: source.phone ? "Phone number" : "Endpoint", value: source.runtime.where, mono: true });
  }
  const prompts = fagiPromptsOf(agent);
  rows.push({
    label: "Future AGI prompts",
    value: prompts.length ? prompts.map((p) => `${p.name} ${p.version}`).join(", ") : "None loaded",
    muted: !prompts.length,
  });
  return rows;
};

/** For code: what the snapshot holds — the prompts, tools and model we found. */
export const snapshotLineOf = ({ definition, files }) => {
  const prompts = (files || []).filter((f) => f.group === "Prompts").length;
  const tools = (definition?.tools?.items || []).length;
  const model = definition?.model?.visible ? definition.model.name : null;
  return [`${prompts} prompt${prompts === 1 ? "" : "s"}`, `${tools} tool${tools === 1 ? "" : "s"}`, model].filter(Boolean).join(" · ");
};

/** Reach rows: channel, web or phone call, call direction, who speaks first, numbers, conversations at once. */
export const reachRowsOf = (agent) => {
  const r = reachOf(agent);
  const v = agent.values || {};
  const channels = agentChannelsOf(agent);
  const rows = [];
  if (channels.includes("voice")) {
    /* Saved on Connections as contact.mode and contact.numbers; a number
       connected as the agent itself is a phone call too. */
    const numbers = contactNumbersOf(agent).map(phoneLabelOf);
    const phone = v.contact?.mode === "phone" || v.contact?.mode === "phone-only" || !!v.phoneNumber || agent.sourceKind === "phone";
    const direction = (DIRECTIONS.find((d) => d.id === r.direction)?.label || "Inbound").toLowerCase();
    rows.push(
      { label: "Voice", value: `${phone ? "Phone call" : "Web call"} · ${direction} · ${r.whoSpeaksFirst === "agent" ? "agent" : "caller"} speaks first` },
      {
        label: "Phone numbers",
        value: numbers.length ? numbers.join(", ") : phone ? "None saved — simulations can't call it yet" : "None — reached by web call",
        mono: numbers.length > 0,
        muted: !numbers.length,
      },
    );
  }
  if (channels.includes("chat")) {
    rows.push({ label: "Chat", value: `${r.whoWritesFirst === "agent" ? "Agent" : "User"} writes first · ${turnLimitLabel(r.turnLimit).toLowerCase()}` });
  }
  rows.push({ label: "At once", value: `Up to ${r.maxConcurrent} conversation${r.maxConcurrent === 1 ? "" : "s"}` });
  return rows;
};

/** Every configuration area that applies, as read for this version. */
export const configRowsOf = ({ agent, definition, source }) => configAreasFor(agent).map((a) => ({
  id: a.id,
  label: a.label,
  ...areaValueOf({ agent, definition, source, id: a.id }),
}));
