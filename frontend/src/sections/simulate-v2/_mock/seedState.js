/**
 * Environments the user already has.
 *
 * A brand-new workspace with an empty "My environments" tab hides half the
 * product — the workspace, the runs, the eval mapping — behind setup nobody has
 * done yet. This seeds a few that look like real work in progress: one built
 * from an agent, one adopted from a template and run a few times, one still
 * half configured.
 *
 * Only used when nothing has been persisted yet, so it never overwrites what
 * someone actually did.
 */

import { getEnvironment } from "./environments";
import { getRows } from "./scenarios";
import { derivedEnvironment } from "./builder";

const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString();

const rowsFor = (env, kinds) =>
  kinds.flatMap((k) => getRows(`${env.id}::${k}`, env));

/*
  A run records which agent version it ran, the seed it ran with, which
  scenarios it covered and how many times it ran each of them. Without the
  version a history is a pile of numbers you cannot scope; without the seed a
  failing scenario cannot be replayed; without the scenario ids a comparison
  will happily line up a 3-scenario smoke run against a full sweep and call the
  difference a regression.
*/
const run = (id, label, finishedAt, scenarios, passed, agentVersion = "v3", seed = 7, ordinal = 1, repeats = 3) => ({
  id,
  label,
  /* Stamped, not counted at render time: deleting run 2 must not turn run 3
     into run 2 in every screenshot anyone has already taken. */
  ordinal,
  finishedAt,
  total: scenarios.length,
  passed,
  agentVersion,
  seed,
  repeats,
  scenarioIds: scenarios.map((sc) => sc.id),
});

/* Agent versions are minted when the agent changes, so a seeded history needs
   them stated — the runs below pin labels out of this list. */
const agentVersion = (n, daysOld, note) => ({
  id: `agent-v${n}`,
  label: `v${n}`,
  note,
  reach: "endpoint",
  createdAt: daysAgo(daysOld),
});

export function seededState() {
  const built = {
    ...derivedEnvironment({ kind: "repo", value: "https://github.com/acme/returns-line-agent" }),
    adoptedAt: daysAgo(2),
  };
  const browser = { ...getEnvironment("env-browser"), adoptedAt: daysAgo(6) };
  const banking = { ...getEnvironment("env-chat-banking"), adoptedAt: daysAgo(1) };
  const travel = { ...getEnvironment("env-chat-travel"), adoptedAt: daysAgo(4) };

  const builtScenarios = rowsFor(built, ["core", "rules"]);
  /* The earlier run was a rule-probe pass, not a full sweep. */
  const builtRuleProbes = rowsFor(built, ["rules"]).slice(0, 3);
  const browserScenarios = rowsFor(browser, ["core", "traps"]);
  const bankingScenarios = rowsFor(banking, ["core"]);
  const travelScenarios = rowsFor(travel, ["core", "rules"]);
  /* The first run only probed the entitlement rules — the expensive ones. */
  const travelRuleProbes = rowsFor(travel, ["rules"]).slice(0, 4);

  return {
    myEnvironments: [built, travel, banking, browser],
    byEnv: {
      // built from an agent, fully set up, run twice
      [built.id]: {
        agent: {
          typeId: "voice_platform",
          /* Keys must match voice_platform's field keys — `direction` was not
             one, so the seeded agent rendered a single row. */
          values: { provider: "livekit", agentId: "asst_9f2c1188", callDirection: "inbound" },
          via: "endpoint",
          connectedAt: daysAgo(2),
        },
        scenarios: builtScenarios,
        scenarioSource: "agent",
        /*
          Kept empty so a user landing on the Evaluations tab of a
          seeded env sees the same "Suggested (unadded) + Added
          (empty)" layout a fresh env starts with. The workspace
          header no longer surfaces an "Add evaluations to run" chip
          for this (only "Live"), so an empty evals array here is
          purely a UI state, not a broken-setup signal.
        */
        evals: [],
        agentVersions: [
          agentVersion(1, 12, "First version connected to this environment."),
          agentVersion(2, 6, "Goodwill cap moved out of the prompt and into code."),
          agentVersion(3, 2, "Refund quoting moved before the promise."),
        ],
        runs: [
          run("run-r2", "All scenarios", daysAgo(1), builtScenarios, builtScenarios.length - 1, "v3", 7, 2),
          run("run-r1", "Rule probes", daysAgo(2), builtRuleProbes, 2, "v2", 41, 1),
        ],
      },

      /*
        A chat environment that is actually wired up. The other chat one is
        deliberately half-configured — it shows what setup looks like before
        anyone has done it — which left nothing to demonstrate the chat run,
        the chat transcript or a chat comparison against.
      */
      [travel.id]: {
        agent: {
          typeId: "chat_webhook",
          values: {
            endpoint: "https://api.skyline-air.com/agent/chat",
            auth: "bearer",
            messagePath: "$.choices[0].message.content",
            sessionPath: "$.session_id",
            streaming: true,
          },
          via: "endpoint",
          connectedAt: daysAgo(4),
        },
        scenarios: travelScenarios,
        scenarioSource: "templates",
        /*
          Kept empty so a user landing on the Evaluations tab of a
          seeded env sees the same "Suggested (unadded) + Added
          (empty)" layout a fresh env starts with. The workspace
          header no longer surfaces an "Add evaluations to run" chip
          for this (only "Live"), so an empty evals array here is
          purely a UI state, not a broken-setup signal.
        */
        evals: [],
        agentVersions: [
          agentVersion(1, 9, "First version connected to this environment."),
          agentVersion(2, 4, "Entitlement check moved ahead of the compensation offer."),
        ],
        runs: [
          run("run-t2", "All scenarios", daysAgo(1), travelScenarios, travelScenarios.length - 2, "v2", 23, 2),
          run("run-t1", "Entitlement probes", daysAgo(4), travelRuleProbes, 2, "v1", 88, 1),
        ],
      },

      // adopted template, agent connected over MCP, one run
      [browser.id]: {
        agent: {
          typeId: "browser_agent",
          values: { framework: "playwright", viewport: "1280x800", recordVideo: true },
          via: "mcp",
          connectedAt: daysAgo(5),
        },
        scenarios: browserScenarios,
        scenarioSource: "templates",
        /*
          Kept empty so a user landing on the Evaluations tab of a
          seeded env sees the same "Suggested (unadded) + Added
          (empty)" layout a fresh env starts with. The workspace
          header no longer surfaces an "Add evaluations to run" chip
          for this (only "Live"), so an empty evals array here is
          purely a UI state, not a broken-setup signal.
        */
        evals: [],
        agentVersions: [
          agentVersion(1, 9, "First version connected to this environment."),
          agentVersion(2, 5, "Selector strategy switched to role-based queries."),
        ],
        runs: [run("run-b1", "Core tasks", daysAgo(4), browserScenarios, browserScenarios.length, "v2", 12, 1)],
      },

      // half set up — scenarios chosen, no agent yet
      [banking.id]: {
        agent: null,
        scenarios: bankingScenarios,
        scenarioSource: "templates",
        /*
          Kept empty so a user landing on the Evaluations tab of a
          seeded env sees the same "Suggested (unadded) + Added
          (empty)" layout a fresh env starts with. The workspace
          header no longer surfaces an "Add evaluations to run" chip
          for this (only "Live"), so an empty evals array here is
          purely a UI state, not a broken-setup signal.
        */
        evals: [],
        agentVersions: [],
        runs: [],
      },
    },
    activeRun: null,
  };
}
