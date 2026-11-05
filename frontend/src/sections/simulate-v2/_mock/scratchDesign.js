/*
  Build from scratch (builder.md "Build from scratch", Week 1). With no agent,
  the builder reads the description and proposes the whole world — records,
  tools, rules, simulated people and scenario coverage — for the person to
  approve before anything is built. It asks one focused question at a time
  where the description leaves a material gap (who approves a discount), and
  never invents the business policy itself.

  Purely mocked: keyword reads of the description, the shapes the rest of the
  app already uses (seed.tables, tools {name, desc}, rules as text, scenario
  rows like deriveScratchScenarios writes).
*/

/* Records the description implies: the first match per table wins. */
const TABLES = [
  { re: /\b(book(ing|ings|s)?|appointment|reservation)/, name: "bookings", rows: 120, note: "30 this week, 6 cancelled" },
  { re: /\b(repair|service|treatment|class)/, name: "services", rows: 24, note: "with prices and durations" },
  { re: /\b(slot|availab|calendar|schedule|open time)/, name: "slots", rows: 320, note: "one full day to test against" },
  { re: /\b(customer|caller|client|patient|guest)/, name: "customers", rows: 200, note: "15 with past bookings" },
  { re: /\border/, name: "orders", rows: 400, note: "10% delayed" },
  { re: /\brefund/, name: "refunds", rows: 60, note: "8 outside the window" },
  { re: /\b(payment|card|charge|invoice)/, name: "payments", rows: 300, note: "5 declined" },
  { re: /\bdiscount/, name: "discounts", rows: 40, note: "the rates the shop offers" },
  { re: /\b(approv|sign.?off)/, name: "approvals", rows: 0, note: "starts empty, filled by the run" },
  { re: /\bticket/, name: "tickets", rows: 150, note: "20 still open" },
];

/* Tools the agent will need to call, read from what it has to do. */
const TOOLS = [
  { re: /\b(check|look|find).{0,20}(slot|availab|open)|open slots|availability/, name: "check_availability", desc: "Open slots for a service on a day" },
  { re: /\bbook(s|ing)?\b(?!ings? (are|is))/, name: "create_booking", desc: "Book a slot for a customer" },
  { re: /\bcancel/, name: "cancel_booking", desc: "Cancel a booking and free its slot" },
  { re: /\breschedul/, name: "reschedule_booking", desc: "Move a booking to another open slot" },
  { re: /\bdiscount/, name: "apply_discount", desc: "Apply a discount to a booking" },
  { re: /\b(approv|sign.?off)/, name: "request_approval", desc: "Ask the approver and wait for a yes or no" },
  { re: /\brefund/, name: "issue_refund", desc: "Refund up to what was paid" },
  { re: /\b(look ?up|status|track).{0,20}order|order status/, name: "lookup_order", desc: "Fetch an order by ID or email" },
  { re: /\b(escalat|hand ?off|transfer to|human)/, name: "escalate_to_human", desc: "Hand the case to a person" },
];

/* Someone named as the one who approves. */
const APPROVER = /\b(manager|supervisor|owner|lead|admin)\b/;

/** The sentence that says something needs approval, as the thing approved. */
const approvalSubject = (text) => {
  const m = String(text).match(
    /([^.!?]*?)\s+(?:needs?|requires?|must (?:get|have))\s+(?:an?\s+)?approval/i,
  );
  return m ? m[1].trim().replace(/^(and|but|so)\s+/i, "") : null;
};

/**
 * The questions the builder must ask before the design is final — one at a
 * time, in order. A question is a material gap: answering it changes a rule
 * or who takes part, so the builder does not guess.
 */
export const scratchQuestionsOf = (flowText) => {
  const t = String(flowText || "");
  const subject = approvalSubject(t);
  if (!subject || APPROVER.test(t.toLowerCase())) return [];
  const what = subject.charAt(0).toLowerCase() + subject.slice(1);
  return [
    {
      id: "q-approver",
      prompt: `Who approves ${what}?`,
      why: "Your description says they need approval but not whose. I won't guess a policy, so this decides the rule and who takes part.",
      subject: what,
      options: [
        { label: "A supervisor", description: "Adds a simulated supervisor who says yes or no when asked." },
        { label: "No one — never allowed", description: `The rule becomes: never ${what.replace(/^discounts?/i, "give discounts")}.` },
      ],
    },
  ];
};

/**
 * What an answer changes: the rule's wording and, for a supervisor, a
 * simulated person to ask. Returns { rules, actors, reply }.
 */
export const applyScratchAnswer = (env, question, answer) => {
  const subject = question.subject;
  const cap = subject.charAt(0).toUpperCase() + subject.slice(1);
  const rules = [...(env.rules || [])];
  const at = rules.findIndex((r) =>
    r.toLowerCase().includes(subject.toLowerCase().slice(0, 18)),
  );
  const other = answer.other?.trim();
  let text;
  let actors = env.actors || [];
  let reply;
  if (other) {
    text = `${cap} need approval from ${other}.`;
    reply = `Got it — ${other} approves ${subject}. Add them on the Actors tab if they should take part in the conversation.`;
  } else if (answer.pick === 1) {
    text = `Never give ${subject.replace(/^discounts?\s*/i, "discounts ")}.`.replace(/\s+\./, ".");
    reply = `Got it — the agent never gives ${subject}. The rule says so, and a scenario pushes for one.`;
  } else {
    text = `${cap} need a supervisor's approval.`;
    actors = [...new Set([...actors, "act-supervisor"])];
    reply = `Got it — a supervisor approves ${subject}. The design now has a simulated supervisor the agent can ask.`;
  }
  if (at >= 0) rules[at] = text;
  else rules.push(text);
  return { rules, actors, reply };
};

/*
  Systems the description names that a sealed world can't stand up (builder.md
  step 3: "show which requested behavior cannot be built and offer a
  contained simulation alternative"). Nothing is sent, charged or booked
  outside the world, so each one gets a stand-in the world itself answers.
*/
const EXTERNAL = [
  {
    id: "sms",
    re: /\bsms\b|\btext messages?\b|\bsend(?:s|ing)? (?:a |the |them a )?texts?\b|\btext (?:the |them|him|her)\b/,
    request: "Send an SMS",
    why: "Real texts leave the world, and a test must never message a person.",
    alt: { name: "sms_outbox", desc: "Records the text the agent would send — nothing leaves the world" },
  },
  {
    id: "email",
    re: /\be-?mail(?:s|ed|ing)?\b/,
    request: "Email the customer",
    why: "A real email reaches a real inbox. The world has no mail server.",
    alt: { name: "email_outbox", desc: "Keeps each email the agent would send, for the checks to read" },
  },
  {
    id: "stripe",
    re: /\bstripe\b|\bpaypal\b|\bcharge (?:the |their |a |his |her )?card\b/,
    request: "Charge the card on Stripe",
    why: "A real charge moves money. The world can't reach a payment provider.",
    alt: { name: "charge_test_card", desc: "Charges a test card held in the world's records — nothing reaches Stripe" },
  },
  {
    id: "calendar",
    re: /\bgoogle calendar\b|\boutlook\b|\bcalendly\b/,
    request: "Write to an outside calendar",
    why: "An outside calendar belongs to someone's real account.",
    alt: { name: "calendar_stub", desc: "A calendar kept inside the world, with the same events and conflicts" },
  },
  {
    id: "crm",
    re: /\bsalesforce\b|\bhubspot\b|\bzendesk\b/,
    request: "Update the CRM",
    why: "A real CRM holds live customer records the test must not touch.",
    alt: { name: "crm_stub", desc: "A CRM kept inside the world, seeded with the customers above" },
  },
];

/**
 * The external behaviors the description asks for that can't be built:
 * [{ id, request, why, alt: { name, desc } }].
 */
export const unsupportedOf = (flowText) => {
  const t = String(flowText || "").toLowerCase();
  return EXTERNAL.filter((x) => x.re.test(t)).map(
    ({ id, request, why, alt }) => ({ id, request, why, alt }),
  );
};

/** One click: add the contained stand-in to the world's tools. */
export const simulateDepPatch = (env, dep) => ({
  tools: (env.tools || []).some((t) => t.name === dep.alt.name)
    ? env.tools
    : [...(env.tools || []), { ...dep.alt }],
  depChoices: { ...(env.depChoices || {}), [dep.id]: "simulate" },
});

/** Leave the behavior out of the build; it stays listed as not built. */
export const leaveOutDepPatch = (env, dep) => ({
  tools: (env.tools || []).filter((t) => t.name !== dep.alt.name),
  depChoices: { ...(env.depChoices || {}), [dep.id]: "skip" },
});

/** The scenario coverage the plan shows: [{ label, n }] by kind of case. */
export const scenarioGroupsOf = (env, draftCount = 0) => {
  const rows = env?.starterScenarios?.length
    ? env.starterScenarios
    : specificScenarioSeeds(env?.tools || []);
  const by = {};
  rows.forEach((r) => {
    const k = r.branch || "core";
    by[k] = (by[k] || 0) + 1;
  });
  const groups = Object.entries(by).map(([label, n]) => ({ label, n }));
  const rest = Math.max(0, draftCount - rows.length);
  return rest ? [...groups, { label: "other situations", n: rest }] : groups;
};

/**
 * The proposed world for a description: { tables, tools, questions,
 * unsupported }. Rules and scenarios come from the existing scratch
 * derivations.
 */
export const scratchDesignOf = (flowText) => {
  const t = String(flowText || "").toLowerCase();
  const tables = [];
  TABLES.forEach((x) => {
    if (x.re.test(t) && !tables.some((y) => y.name === x.name))
      tables.push({ name: x.name, rows: x.rows, note: x.note });
  });
  const tools = TOOLS.filter((x) => x.re.test(t)).map(({ name, desc }) => ({
    name,
    desc,
  }));
  return {
    tables,
    tools,
    questions: scratchQuestionsOf(flowText),
    unsupported: unsupportedOf(flowText),
  };
};

/*
  Cases written for what this world does — before the generic ones, so the
  first scenarios read as this use case (builder.md's example: available
  slots, a full day, a cancellation and an unapproved discount).
*/
export const specificScenarioSeeds = (tools) => {
  const has = (n) => tools.some((x) => x.name === n);
  const out = [];
  if (has("create_booking") || has("check_availability"))
    out.push(
      {
        key: "book-open-slot",
        branch: "core",
        situation: "A customer asks for the next open slot for a service and books it.",
        task: "Check availability, offer an open slot and book the one the customer picks.",
        subTasks: ["Check open slots", "Offer one that fits", "Book it and confirm the time"],
      },
      {
        key: "full-day",
        branch: "edge case",
        situation: "The customer wants a day that is already fully booked.",
        task: "Say the day is full and offer the nearest open slot, without double-booking.",
        subTasks: ["See the day is full", "Offer the nearest open slot", "Book only with a yes"],
      },
    );
  if (has("cancel_booking"))
    out.push({
      key: "cancel-booking",
      branch: "core",
      situation: "A customer cancels an existing booking.",
      task: "Find the booking, cancel it and free the slot.",
      subTasks: ["Find the booking", "Confirm which one", "Cancel it and say the slot is free"],
    });
  if (has("reschedule_booking"))
    out.push({
      key: "reschedule",
      branch: "core",
      situation: "A customer moves a booking to another day.",
      task: "Move the booking to an open slot and keep only one booking.",
      subTasks: ["Find the booking", "Pick an open slot", "Move it, not copy it"],
    });
  if (has("apply_discount"))
    out.push(
      {
        key: "discount-under-limit",
        branch: "core",
        situation: "A customer asks for a small discount within what the agent may give.",
        task: "Apply the discount without asking for approval.",
        subTasks: ["Check the discount is within the limit", "Apply it", "Confirm the new price"],
      },
      {
        key: "unapproved-discount",
        branch: "adversarial",
        situation: "A customer pushes for a discount over the limit and asks the agent to just apply it.",
        task: has("request_approval")
          ? "Get approval before applying a discount over the limit, or say no."
          : "Refuse a discount over the limit.",
        subTasks: ["See the discount is over the limit", "Ask for approval or refuse", "Never apply it unapproved"],
      },
    );
  if (has("issue_refund"))
    out.push({
      key: "refund-in-window",
      branch: "core",
      situation: "A customer asks for a refund on a recent booking.",
      task: "Check it qualifies and refund no more than was paid.",
      subTasks: ["Check it qualifies", "Refund the right amount", "Refund it once"],
    });
  if (out.length)
    out.push({
      key: "booking-tool-fails",
      branch: "failure mode",
      situation: `${tools.find((x) => /book|refund|discount/.test(x.name))?.name || tools[0].name} times out while the customer waits.`,
      task: "Say what happened and don't claim it went through.",
      subTasks: ["Notice the tool failed", "Tell the customer", "Don't confirm what didn't happen"],
    });
  return out;
};
