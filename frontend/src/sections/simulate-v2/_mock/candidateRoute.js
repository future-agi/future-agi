/*
  Bring your own improver — what an outside coding agent submits as a
  candidate, and how Future AGI tests it, for every way an agent comes in.

  The RSI PRD says "each time the agent wants proof, it submits the candidate;
  we run baseline and candidate on the same scenarios". The question raised on
  it (Chintan, Atharva — 6 Oct) was how that happens when there's no
  repository to push to. The answer: the coding agent always works on what
  defines the agent, and we always test it the same way our own engine tests
  its candidates — never on the live agent.

    commit    — code in a repository: a commit it pushed to a branch (or its
                draft PR's head commit). We build that commit into a sealed
                snapshot and run it next to today's version.
    archive   — uploaded code: an archive of the patched code.
    settings  — a hosted platform (Vapi, Retell, ElevenLabs, Bland): a
                settings patch through `submit_candidate` — instructions, tool
                descriptions, the settings the change policy allows. We apply
                it to a test copy (Vapi) or a draft version (Retell) in the
                customer's account and run it next to the live settings. A
                platform kept as code in GitHub is a repository — `commit`.
    version   — built in Future AGI: a draft prompt or agent version, never
                active, run next to the active one.
    deployed  — an endpoint or phone number we can only call: the address or
                version it deployed. We run our scenarios; we can't see what
                runs there, so the proof says Deployed by you.
    local     — local code through our SDK: the commit plus a fingerprint of
                uncommitted changes, run on the customer's machine.
*/
import { PROVIDER_LABELS, definitionOf, sourceKindOf } from "./agentRegistry";

const TEST_COPY = {
  vapi: "a test copy",
  retell: "a draft version",
  elevenlabs: "a branch",
  bland: "a branch",
};
const SHIPS_AS = {
  vapi: "a Vapi copy for you to swap in",
  retell: "an unpublished Retell version",
  elevenlabs: "an ElevenLabs branch",
  bland: "a Bland branch",
};

/** { id, submits, tested, ships, tier } for the agent. */
export const candidateRouteOf = (agent) => {
  const kind = sourceKindOf(agent);
  const def = definitionOf(agent) || {};
  const v = agent?.values || {};
  if (kind === "framework" && v.runLocation === "sdk" && v.localSdk) {
    return {
      id: "local",
      tier: "local",
      submits: "the commit it's on, plus a fingerprint of uncommitted changes",
      tested:
        "Our SDK runs it on your machine against the same scenarios; we score it.",
      ships: "a pull request from your machine",
    };
  }
  if (kind === "repo" || (kind === "framework" && def.repoUrl)) {
    return {
      id: "commit",
      tier: "future-agi",
      submits: "a commit it pushed to a branch — or its draft PR's head commit",
      tested:
        "We build that commit into a sealed snapshot and run it next to today's version on the same scenarios.",
      ships: "its pull request, carrying our proof",
    };
  }
  if (kind === "upload") {
    return {
      id: "archive",
      tier: "future-agi",
      submits: "an archive of the patched code",
      tested:
        "We store it as a snapshot and run it next to today's version on the same scenarios.",
      ships: "a patch for you to upload",
    };
  }
  if (kind === "platform" && TEST_COPY[v.provider]) {
    const label = PROVIDER_LABELS[v.provider] || "the platform";
    return {
      /* A live assistant isn't ours to control; a sealed imported copy is. */
      id: "settings",
      tier: v.importedCopy ? "future-agi" : "deployed",
      submits: `a settings patch — the ${label} settings the change policy allows`,
      tested: `We apply it to ${TEST_COPY[v.provider]} in your ${label} account — never the live assistant — and run it next to the live settings.`,
      ships: SHIPS_AS[v.provider],
    };
  }
  if (kind === "builtin") {
    return {
      id: "version",
      tier: "future-agi",
      submits: "a draft prompt or agent version",
      tested:
        "We save it as a draft — never active — and run it next to the active version.",
      ships: "a draft version a workspace admin activates",
    };
  }
  return {
    id: "deployed",
    tier: "deployed",
    submits: "the address or version it deployed",
    tested:
      "We run our scenarios against it. We can't see what runs there, so the proof says Deployed by you.",
    ships: "your own deploy",
  };
};

/** The brief's "Submit a candidate" step, in the words of the route. */
export const submitStepOf = (agent) => {
  const r = candidateRouteOf(agent);
  /* Every route goes through the same MCP tool — our MCP server or the
     Claude Code plugin — never a CLI of ours. */
  const how = {
    commit:
      "Push a branch, then call the `submit_candidate` tool with the commit.",
    archive:
      "Call the `submit_candidate` tool with an archive of the patched code.",
    settings:
      "Call the `submit_candidate` tool with the settings you changed, as a patch. Don't edit the live assistant — we apply your patch to a test copy.",
    version:
      "Call the `submit_candidate` tool with the new prompt or agent definition. It's saved as a draft, never made active.",
    deployed:
      "Deploy your change, then call the `submit_candidate` tool with its address or version.",
    local:
      "Call the `submit_candidate` tool from your working copy with the commit you're on.",
  }[r.id];
  return `${how} (The tool comes with the Future AGI MCP server and Claude Code plugin.) ${r.tested} Future AGI computes the verdict — read it with \`get_verdict\`.`;
};

/** The same, for the three ways listed on the Coding agents page. */
export const CANDIDATE_ROUTES = [
  {
    source: "Code in a repository",
    submits: "A commit on a branch, or its draft PR",
    tested: "Built into a sealed snapshot, run next to today's version",
  },
  {
    source: "Uploaded code",
    submits: "An archive of the patched code",
    tested: "Stored as a snapshot, run next to today's version",
  },
  {
    source: "Vapi, Retell and other hosted platforms",
    submits: "A settings patch, through submit_candidate",
    tested:
      "Applied to a test copy or draft version in your account — never the live assistant",
  },
  {
    source: "Built in Future AGI",
    submits: "A draft prompt or agent version",
    tested: "Saved as a draft, never active, run next to the active version",
  },
  {
    source: "Endpoint or phone number",
    submits: "The address or version it deployed",
    tested: "Our scenarios against it — the proof says Deployed by you",
  },
];
