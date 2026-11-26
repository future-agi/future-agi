// The builder's read-only chat on a design, shared by the design page and
// the environment page its approved build opens.

const names = (list) => list.map((f) => f.name).join(", ");
const count = (n, one, many = `${one}s`) => n && `${n} ${n === 1 ? one : many}`;

// What an agent's build read from it, as the builder's tool steps.
function agentTurn(agent, summary) {
  const tools = summary.tools;
  const rules = summary.rules;
  return {
    id: "agent",
    role: "builder",
    title: "Understanding the agent",
    steps: [
      {
        kind: "note",
        text: `Reading the agent's config${agent.source ? ` from ${agent.source}` : ""}: the prompt, tools and settings ${agent.name} runs with.`,
      },
      {
        kind: "tool",
        label: "read_config",
        state: "completed",
        result: [agent.source, agent.name, agent.version]
          .filter(Boolean)
          .join(" · "),
      },
      {
        kind: "tool",
        label: "extract_tools",
        state: tools ? "completed" : "running",
        result: tools
          ? [
              count(tools.length, "tool") || "No tools",
              ...tools.map((t) => t.name),
            ].join(" · ")
          : "",
      },
      {
        kind: "tool",
        label: "extract_rules",
        state: rules ? "completed" : "running",
        result: rules
          ? `${count(rules.length, "rule") || "No rules"} · read from what to test`
          : "",
      },
      {
        kind: "note",
        text: "Contract written from what we read, not from a form. Rules stated only in a prompt are graded rather than guaranteed, so they get the hardest scenarios.",
      },
    ],
  };
}

// What the builder says once the design is approved, by build status.
function buildTurn(build) {
  const failed = build.validation?.checks.find((c) => !c.ok);
  const text = {
    building:
      "Approved. Building the records and data, tool handlers, rules and checks, then the scenarios. It's checked, then you review and save it.",
    ready: "Built and checked. Review the changes and save them as v1.",
    saved: `Built and checked, and saved as v${build.release?.version}. Nothing runs until you connect an agent and start a run.`,
    needs_repair: [
      failed && `A check failed: ${failed.label.toLowerCase()}.`,
      failed?.detail,
      "Nothing was saved and the draft is kept, so you can fix it and approve again.",
    ]
      .filter(Boolean)
      .join(" "),
    failed: `Building ${build.failure?.stage.toLowerCase()} failed: ${build.failure?.detail} Nothing was saved and the draft is kept, so you can fix it and approve again.`,
    cancelled:
      "The build was cancelled. Nothing was saved and the draft is kept, so you can approve it again.",
  }[build.status === "validating" ? "building" : build.status];
  return { id: "build", role: "builder", steps: [{ kind: "note", text }] };
}

// The builder's turns on a design, read only: an agent's build first says
// what it read from the agent, then what it found, then its first open
// question, then how the approved build went. Nothing is said before the page
// shows it.
export function turnsOf(draft, summary, build) {
  const turns = draft.agent ? [agentTurn(draft.agent, summary)] : [];
  const reading = summary.readingParts.length > 0;
  let text;
  if (reading) {
    text = `Reading ${summary.readingNames}. I'll say what I found in a moment.`;
  } else {
    const unread = summary.unusable.length
      ? ` I couldn't read ${names(summary.unusable)}.`
      : "";
    const read = names(summary.sources);
    const ask =
      "What does the agent work with, and what can it do? For example: customers and their orders; it can look up an order and refund up to $200.";
    const counts = [
      count(summary.records.length, "kind of record", "kinds of record"),
      count(summary.tools.length, "tool"),
      count(summary.rules.length, "rule"),
    ].filter(Boolean);
    const found = counts.length
      ? `The design has ${counts.join(", ")}, each saying where it came from.${unread} It's on the right.`
      : `No records, tools or rules yet.${unread} ${
          summary.brief.agent
            ? "The tools are read from the agent's code when it builds."
            : "The files go in as background for the agent."
        }`;
    text = summary.approveBlocked
      ? `There's nothing to build yet.${unread} ${ask}`
      : `${read ? `I read ${read}. ` : ""}${found} Nothing is built until you approve.`;
  }
  turns.push({ id: "found", role: "builder", steps: [{ kind: "note", text }] });
  // One question at a time: only the first open one is asked.
  const q = summary.questions[0];
  if (q)
    turns.push({
      id: q.id,
      role: "builder",
      steps: [
        {
          kind: "note",
          markdown: true,
          text: [
            `One question first. ${q.why}`,
            `**${q.prompt}**`,
            q.options.map((o) => `- ${o.label}: ${o.description}`).join("\n"),
          ].join("\n\n"),
        },
      ],
    });
  if (build) turns.push(buildTurn(build));
  return turns;
}
