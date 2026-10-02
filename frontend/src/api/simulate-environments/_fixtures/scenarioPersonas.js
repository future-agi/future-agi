// Scenario personas (verbatim port of the designer scenarios fixture, 9-105).
//
// Personas are no longer a separate object the user has to assemble — each
// scenario row carries its persona, because a scenario without a person in it
// was never actually runnable. One row = one task the agent must complete.
//
// Personas come in two shapes, because "who is on the other end" differs by
// surface. On a phone line it is a customer with an age and an accent the agent
// has to cope with. In a terminal or a warehouse there is no caller — there is a
// colleague filing a request, and what matters is how they brief: terse, vague,
// urgent. A 34-year-old with a US accent means nothing to a `run_tests` task, so
// those environments get requesters instead.

// Persona traits we surface as chips on a scenario row.
export const PERSONA_TRAITS = [
  "impatient", "polite", "confused", "angry", "elderly", "accented",
  "talks over", "background noise", "distracted", "sceptical", "chatty",
  "non-native speaker", "hard of hearing", "in a hurry", "tests boundaries",
];

// Personas are archetypes — the caller isn't "Marcus Webb, 34", it's "The
// Polite Senior Caller". That reads as a persona spec ("who is on the other
// end") instead of a made-up human, which is what a scenario brief actually
// needs: a shape the simulator can play, not a name. The slug is a compact
// kebab id used to prefix scenario names.
const P = (name, slug, age, traits, voice) => ({ name, slug, age, traits, voice });

// Customer on a conversational channel.
export const CUSTOMER_POOL = [
  P("The Polite Senior Caller", "polite-senior", 68, ["polite", "elderly", "hard of hearing"], "US female"),
  P("The Hungry Customer in a Rush", "hungry-rushed", 34, ["impatient", "in a hurry"], "US male"),
  P("The Impatient Truck Driver", "impatient-driver", 45, ["impatient", "background noise", "distracted"], "US male"),
  P("The Delivery Driver on the Move", "delivery-mobile", 29, ["in a hurry", "background noise", "distracted"], "US male"),
  P("The Local Restaurant Owner", "restaurant-owner", 52, ["chatty", "assumes context"], "UK male"),
  P("The Tech-Savvy Young Professional", "tech-savvy-pro", 27, ["sceptical", "tests boundaries"], "US female"),
  P("The Telecom Customer in Distress", "telecom-distress", 41, ["angry", "confused"], "IN female"),
  P("The Hustling Homemaker", "hustling-homemaker", 38, ["in a hurry", "chatty"], "US female"),
  P("The Emotional Loyalist", "emotional-loyalist", 55, ["chatty", "polite"], "US female"),
  P("The Reserved Senior", "reserved-senior", 71, ["polite", "elderly", "hard of hearing"], "UK female"),
  P("The Frustrated Everyday User", "frustrated-user", 40, ["angry", "impatient"], "US male"),
  P("The Confused First-Time User", "first-time-user", 33, ["confused", "non-native speaker"], "BR female"),
  P("The Frustrated Subscriber", "frustrated-subscriber", 47, ["angry", "sceptical"], "US male"),
  P("The Curious Evaluator", "curious-evaluator", 36, ["sceptical", "tests boundaries", "chatty"], "US female"),
];

// Colleague filing a request against a technical environment.
const R = (name, slug, role, traits) => ({ name, slug, role, traits });

export const REQUESTER_POOL = [
  R("The Terse Staff Engineer", "staff-engineer", "Staff engineer", ["terse", "assumes context"]),
  R("The Scope-Shifting PM", "scope-shifting-pm", "Product manager", ["vague requirements", "changes scope"]),
  R("The On-Call SRE Under Pressure", "on-call-sre", "On-call SRE", ["urgent", "interrupt-driven"]),
  R("The Distrustful Data Analyst", "data-analyst", "Data analyst", ["precise", "distrusts the numbers"]),
  R("The Suspicious Security Reviewer", "security-reviewer", "Security reviewer", ["asks for proof", "tests boundaries"]),
  R("The Escalation-Happy Support Lead", "support-lead", "Support lead", ["escalates quickly", "cites ticket IDs"]),
  R("The No-Nonsense Executive", "exec", "Finance controller", ["audit-minded", "detail-oriented"]),
  R("The Delegating Operations Manager", "ops-manager", "Operations manager", ["in a hurry", "delegates detail"]),
  R("The Formal Compliance Officer", "compliance-officer", "Compliance officer", ["formal", "policy-first"]),
  R("The Stressed Accountant", "stressed-accountant", "Junior accountant", ["unsure", "asks follow-ups"]),
  R("The Enterprise IT Admin", "it-admin", "Enterprise IT admin", ["precise", "policy-first"]),
];

// Surfaces where a human is genuinely on the other end of the conversation.
const CONVERSATIONAL = ["voice", "chat", "messaging", "email", "multi"];

// Add derived fields the scenario table shows: `gender` parsed from the voice
// string (customer pools have "US male" / "IN female" etc.), `ageGroup` derived
// from age. Requesters don't carry age/voice so those fields stay null and the
// display falls back to their role.
export const enrichPersona = (p) => {
  if (!p) return p;
  const genderFromVoice = p.voice?.toLowerCase().includes("female")
    ? "female"
    : p.voice?.toLowerCase().includes("male") ? "male" : null;
  const ageGroup = p.age
    ? `${Math.floor(p.age / 10) * 10}-${Math.floor(p.age / 10) * 10 + 10}`
    : null;
  return {
    ...p,
    gender: p.gender || genderFromVoice,
    ageGroup: p.ageGroup || ageGroup,
  };
};

export const personaFor = (env, i) => {
  const pool = CONVERSATIONAL.includes(env?.surface) ? CUSTOMER_POOL : REQUESTER_POOL;
  return enrichPersona(pool[i % pool.length]);
};
