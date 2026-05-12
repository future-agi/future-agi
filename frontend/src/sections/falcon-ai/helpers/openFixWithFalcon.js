import useFalconStore from "../store/useFalconStore";

const SKILL_SLUG = "fix-with-falcon";

const LEVEL_LEAD = {
  eval: "Fix this failing evaluation.",
  span: "Multiple evals failing on this span — diagnose the shared root cause and propose a fix.",
  trace:
    "This trace has quality issues. Investigate all sub-flows and propose a concrete fix.",
  voice:
    "This voice call has issues. Analyze the transcript, span tree, and eval failures, then propose a fix.",
};

function formatValue(v) {
  if (v == null) return null;
  if (Array.isArray(v)) {
    if (v.length === 0) return null;
    return JSON.stringify(v);
  }
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  return s.length > 300 ? `${s.slice(0, 297)}...` : s;
}

function buildContextBlock(fields) {
  const lines = [];
  for (const [k, v] of Object.entries(fields)) {
    const val = formatValue(v);
    if (val != null && val !== "") lines.push(`- ${k}: ${val}`);
  }
  return lines.join("\n");
}

export function openFixWithFalcon({ level, context = {} }) {
  const lead = LEVEL_LEAD[level] || LEVEL_LEAD.trace;
  const ctxBlock = buildContextBlock(context);

  const prompt = [
    `/${SKILL_SLUG} ${lead}`,
    "",
    "Context:",
    ctxBlock,
    "",
    "Investigate and propose a concrete fix.",
  ].join("\n");

  const { setCurrentConversation, setPendingPrompt, openSidebar } =
    useFalconStore.getState();
  setCurrentConversation(null);
  setPendingPrompt(prompt);
  openSidebar();
}
