/**
 * Wrong-way examples (the handover's negative controls) for one scenario:
 * deliberately wrong plays that its checks must fail. Each is listed by
 * name with why it should fail and what happened: Caught, Not caught or
 * Not run. Real data wins — a row's own `negativeControls` are used as is.
 */

const STATUS = {
  caught: { label: "Caught", tone: "success.main" },
  missed: { label: "Not caught", tone: "error.main" },
  notRun: { label: "Not run", tone: "text.disabled" },
};

const statusOf = (ok) =>
  ok === true ? "caught" : ok === false ? "missed" : "notRun";

const quote = (t) => {
  const s = String(t || "").trim();
  return s.length > 72 ? `${s.slice(0, 70).trimEnd()}…` : s;
};

const toolName = (t) => (typeof t === "string" ? t : t?.name) || "";

/**
 * `ctx` = { rule, tools, goals, gates, state }. Returns
 * { examples: [{ id, name, why, status, statusLabel, tone }], none }.
 * `none` explains an empty list (AI-judge-only, tools in the agent's code).
 */
export const wrongWayExamplesOf = (row, ctx = {}) => {
  const { rule, tools = [], goals = [], gates = [], state } = ctx;

  if (row?.judgedOnly)
    return {
      examples: [],
      none: "None — only an AI judge grades this case, so there's no wrong play to check against.",
    };
  if (row?.toolsInAgent)
    return {
      examples: [],
      none: "None — its tools run inside the agent's own code, so a wrong call can't be played here.",
    };

  const gate =
    gates.find((g) => g.id === "vacuous") ||
    gates.find((g) => g.id === "evidence");
  const declines = gate?.id === "evidence";
  const ok = gate ? gate.ok : null;

  const real = Array.isArray(row?.negativeControls)
    ? row.negativeControls.map((c, i) => ({
        id: c.key || c.id || `control-${i}`,
        name: c.name || c.key || `Wrong-way example ${i + 1}`,
        why: c.why || c.expect || "",
        status: c.status || statusOf(c.caught),
      }))
    : null;

  let examples = real;
  if (!examples) {
    if (state?.id === "kept" && !row?.provedAt)
      return {
        examples: [],
        none: "None yet — saved before proofs existed. Validate it to add one.",
      };
    const critical =
      goals.find((g) => g.mark === "critical") || goals.find((g) => g.text);
    const tool = toolName(tools[0]);
    examples = [];
    if (declines)
      examples.push({
        id: "does_what_was_asked",
        name: "Does what was asked",
        why: "Going ahead instead of declining must fail the checks.",
      });
    if (rule?.text)
      examples.push({
        id: "ignores_rule",
        name: "Ignores the rule",
        why: `Breaking “${quote(rule.text)}” must fail the checks.`,
      });
    if (tool && !declines)
      examples.push({
        id: `${tool}_wrong_record`,
        name: `Calls ${tool} on the wrong record`,
        why: critical?.text
          ? `Must fail “${quote(critical.text)}”.`
          : "Must fail the checks.",
      });
    if (!examples.length)
      examples.push({
        id: "does_nothing",
        name: "Does nothing",
        why: "Ending the conversation without acting must fail the checks.",
      });
    /* One gate result covers the set: when it wasn't caught, it's the
       last (most specific) example that slipped through. */
    const last = examples.length - 1;
    examples = examples.map((e, i) => ({
      ...e,
      status: ok === false ? (i === last ? "missed" : "caught") : statusOf(ok),
    }));
  }

  return {
    examples: examples.map((e) => ({
      ...e,
      statusLabel: (STATUS[e.status] || STATUS.notRun).label,
      tone: (STATUS[e.status] || STATUS.notRun).tone,
    })),
    none: null,
  };
};
