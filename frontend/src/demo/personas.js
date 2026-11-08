/* Demo personas for the hosted prototype — the same shape as the Future AGI
   Built ones the API returns, written for the demo (no customer data). */
const P = (id, simulation_type, name, description, f) => ({
  id: `demo-persona-${id}`,
  persona_type: "system",
  persona_type_display: "System",
  name,
  description,
  multilingual: false,
  languages: ["English"],
  background_sound: false,
  keywords: [],
  metadata: {},
  is_default: true,
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-10-30T10:00:00Z",
  simulation_type,
  ...f,
});

const voice = (o) => ({ accent: ["Neutral"], conversation_speed: ["1.0"], finished_speaking_sensitivity: ["5"], interrupt_sensitivity: ["5"], ...o });
const chat = (o) => ({ punctuation: ["Standard"], slang_usage: ["Light"], typos_frequency: ["Rare"], emoji_usage: ["None"], tone: ["Neutral"], verbosity: ["Medium"], ...o });

export const DEMO_PERSONAS = [
  P(1, "voice", "The Enterprise IT Admin", "A focused, analytical engineer managing system reliability and technical escalations.",
    voice({ gender: ["male"], age_group: ["32-40"], occupation: ["Engineer"], location: ["United States"], personality: ["Analytical"], communication_style: ["Technical"], conversation_speed: ["1.25"], finished_speaking_sensitivity: ["7"], interrupt_sensitivity: ["7"], additional_instruction: "Be precise and ask for actionable, technical details." })),
  P(2, "voice", "The Delivery Driver on the Move", "A talkative freelancer multitasking between deliveries, sometimes distracted mid-sentence.",
    voice({ gender: ["male"], age_group: ["25-32"], occupation: ["Driver"], location: ["United Kingdom"], personality: ["Talkative"], communication_style: ["Casual"], accent: ["British"], background_sound: true, interrupt_sensitivity: ["8"] })),
  P(3, "voice", "The Anxious First-Time Caller", "Nervous about getting it wrong; needs reassurance and repeats key details back.",
    voice({ gender: ["female"], age_group: ["18-25"], occupation: ["Student"], location: ["Canada"], personality: ["Anxious"], communication_style: ["Hesitant"], conversation_speed: ["0.9"], finished_speaking_sensitivity: ["3"] })),
  P(4, "voice", "The Frustrated Repeat Caller", "Has called twice about the same issue and wants it fixed now, not explained again.",
    voice({ gender: ["male"], age_group: ["40-50"], occupation: ["Sales manager"], location: ["United States"], personality: ["Impatient"], communication_style: ["Direct"], conversation_speed: ["1.2"], interrupt_sensitivity: ["9"], additional_instruction: "Interrupt long explanations and push for a concrete outcome." })),
  P(5, "voice", "The Retired Traveller", "Polite and unhurried, prefers step-by-step help and confirms each step.",
    voice({ gender: ["female"], age_group: ["65+"], occupation: ["Retired"], location: ["Australia"], personality: ["Patient"], communication_style: ["Formal"], accent: ["Australian"], conversation_speed: ["0.8"] })),
  P(6, "voice", "The Busy Parent", "Calling with children in the background; wants the shortest path to a fix.",
    voice({ gender: ["female"], age_group: ["32-40"], occupation: ["Nurse"], location: ["United States"], personality: ["Pragmatic"], communication_style: ["Brief"], background_sound: true })),
  P(7, "voice", "The Bilingual Small-Business Owner", "Switches between English and Spanish, focused on cost and time.",
    voice({ gender: ["male"], age_group: ["40-50"], occupation: ["Shop owner"], location: ["Mexico"], personality: ["Practical"], communication_style: ["Friendly"], multilingual: true, languages: ["English", "Spanish"], accent: ["Mexican"] })),
  P(8, "voice", "The Skeptical Negotiator", "Questions every policy and asks for exceptions with plausible reasons.",
    voice({ gender: ["female"], age_group: ["32-40"], occupation: ["Lawyer"], location: ["United States"], personality: ["Skeptical"], communication_style: ["Assertive"], additional_instruction: "Challenge refusals and cite earlier promises." })),
  P(9, "text", "The Rushed Mobile User", "Types short messages on a phone, with typos, and expects quick answers.",
    chat({ gender: ["male"], age_group: ["25-32"], occupation: ["Consultant"], location: ["India"], personality: ["Impatient"], communication_style: ["Brief"], typos_frequency: ["Frequent"], verbosity: ["Low"], punctuation: ["Minimal"] })),
  P(10, "text", "The Detail-Oriented Planner", "Writes long, structured messages and wants every condition spelled out.",
    chat({ gender: ["female"], age_group: ["40-50"], occupation: ["Project manager"], location: ["Germany"], personality: ["Meticulous"], communication_style: ["Formal"], verbosity: ["High"] })),
  P(11, "text", "The Casual Emoji Texter", "Friendly and informal, uses slang and emoji, drifts off-topic.",
    chat({ gender: ["female"], age_group: ["18-25"], occupation: ["Designer"], location: ["United States"], personality: ["Playful"], communication_style: ["Casual"], slang_usage: ["Heavy"], emoji_usage: ["Frequent"], tone: ["Warm"] })),
  P(12, "text", "The Angry Escalator", "Opens upset, threatens to cancel, and asks for a manager early.",
    chat({ gender: ["male"], age_group: ["50-65"], occupation: ["Accountant"], location: ["United Kingdom"], personality: ["Angry"], communication_style: ["Confrontational"], tone: ["Hostile"] })),
  /* Two the workspace made itself, for the Custom tab. */
  P(13, "voice", "Gold-tier frequent flyer", "Expects priority handling and knows the fare rules better than most agents.",
    voice({ persona_type: "workspace", persona_type_display: "Custom", is_default: false, updated_at: "2026-11-03T14:00:00Z", gender: ["female"], age_group: ["40-50"], occupation: ["Executive"], location: ["United States"], personality: ["Demanding"], communication_style: ["Direct"], additional_instruction: "Mention your status early and ask for a waiver." })),
  P(14, "text", "Returning customer with a damaged item", "Has photos ready, wants a replacement rather than a refund.",
    chat({ persona_type: "workspace", persona_type_display: "Custom", is_default: false, updated_at: "2026-11-05T11:00:00Z", gender: ["male"], age_group: ["25-32"], occupation: ["Teacher"], location: ["Canada"], personality: ["Calm"], communication_style: ["Friendly"] })),
];

/* Personas made, edited or deleted on the demo, kept in this browser. */
const SAVED = "fagi.demo.personas.v1";
try {
  const { put = [], gone = [] } = JSON.parse(localStorage.getItem(SAVED)) || {};
  put.forEach((p) => { const i = DEMO_PERSONAS.findIndex((x) => x.id === p.id); i >= 0 ? (DEMO_PERSONAS[i] = p) : DEMO_PERSONAS.push(p); });
  gone.forEach((id) => { const i = DEMO_PERSONAS.findIndex((x) => x.id === id); if (i >= 0) DEMO_PERSONAS.splice(i, 1); });
} catch { /* nothing saved */ }
const remember = (fn) => {
  let saved = { put: [], gone: [] };
  try { saved = { ...saved, ...JSON.parse(localStorage.getItem(SAVED)) }; } catch { /* start fresh */ }
  fn(saved);
  try { localStorage.setItem(SAVED, JSON.stringify(saved)); } catch { /* storage unavailable */ }
};

export const savePersona = (body = {}) => {
  const old = DEMO_PERSONAS.find((p) => p.id === body.id);
  const now = new Date().toISOString();
  const p = old
    ? { ...old, ...body, updated_at: now }
    : { ...chat({}), ...body, id: `demo-persona-${Date.now().toString(36)}`, persona_type: "workspace", persona_type_display: "Custom", is_default: false, created_at: now, updated_at: now, languages: body.languages || ["English"], keywords: body.keywords || [], metadata: body.metadata || {} };
  if (old) DEMO_PERSONAS[DEMO_PERSONAS.indexOf(old)] = p; else DEMO_PERSONAS.unshift(p);
  remember((s) => { s.put = [...s.put.filter((x) => x.id !== p.id), p]; });
  return p;
};
export const deletePersona = (id) => {
  const i = DEMO_PERSONAS.findIndex((p) => p.id === id);
  if (i >= 0) DEMO_PERSONAS.splice(i, 1);
  remember((s) => { s.put = s.put.filter((x) => x.id !== id); s.gone = [...new Set([...s.gone, id])]; });
  return {};
};

/* The list endpoint's answer: page, limit, search, type and simulation_type, as the API takes them. */
export const personasPage = (q = {}) => {
  const search = (q.search || "").toLowerCase();
  /* The list asks for "prebuilt" (Future AGI Built) or "custom"; all the demo's are prebuilt. */
  const prebuilt = q.type === "prebuilt" || q.type === "system";
  const rows = DEMO_PERSONAS.filter((p) =>
    (!q.type || (prebuilt ? p.persona_type === "system" : p.persona_type !== "system")) &&
    (!q.simulation_type || p.simulation_type === q.simulation_type) &&
    (!search || `${p.name} ${p.description}`.toLowerCase().includes(search)));
  const limit = Number(q.limit || q.page_size) || 25;
  const page = Number(q.page) || 1;
  return {
    count: rows.length,
    next: page * limit < rows.length ? page + 1 : null,
    previous: page > 1 ? page - 1 : null,
    results: rows.slice((page - 1) * limit, page * limit),
    total_pages: Math.max(1, Math.ceil(rows.length / limit)),
    current_page: page,
  };
};
