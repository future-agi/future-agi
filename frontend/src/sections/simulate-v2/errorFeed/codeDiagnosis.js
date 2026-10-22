/*
  The diagnosis a fix starts from. The feed's root cause names a symptom
  ("the agent said the label was sent"); a search framed on the symptom
  tries the wrong changes. This says where in the agent it goes wrong — the
  prompt section, the tool — why, and what to change, drafted from the
  agent's definition and the issue's call logs. The person can edit it
  before the fix starts; the search starts from what they leave.
*/
import { DEFAULT_SECTIONS } from "../_mock/agentConstraints";
import { issueMechanismOf } from "./issueFixModel";

const headingsOf = (text) => (text || "").split("\n")
  .map((l) => /^#{1,3}\s+(.+?)\s*$/.exec(l.trim())?.[1])
  .filter(Boolean);

/* The prompt's own name for a section, else the default one. */
const sectionNamed = (text, want) => headingsOf(text).find((h) => h.toLowerCase().includes(want.toLowerCase()))
  || DEFAULT_SECTIONS.find((h) => h.toLowerCase().includes(want.toLowerCase())) || want;

/* The first prompt line that matches, quoted as the agent has it. */
const lineMatching = (text, re) => (text || "").split("\n")
  .map((l) => l.trim().replace(/^[-*]\s+/, ""))
  /* Instructions only — not the tool list the prompt carries. */
  .find((l) => l && !l.startsWith("#") && !/\w\(\)/.test(l) && re.test(l)) || null;

export const codeDiagnosisOf = ({ issue, env, promptText = null, rootCause = null }) => {
  if (!issue) return null;
  const m = issueMechanismOf(issue.cluster_id);
  const tools = env?.tools || [];
  const symptom = rootCause?.headline || issue.why_shown || issue.error?.name || null;
  const said = `${symptom || ""} ${issue.error?.name || ""} ${rootCause?.fix || ""}`;
  const skipped = m.missing ? (tools.find((t) => m.match?.test(t.name))?.name || m.missing) : null;
  /* The tool whose result the agent spoke past — named in the issue's own words. */
  const spokePast = tools.find((t) => said.includes(t.name))?.name || null;

  if (skipped) {
    const desc = tools.find((t) => t.name === skipped)?.desc || null;
    const section = sectionNamed(promptText, "Tool use");
    const quote = lineMatching(promptText, new RegExp(skipped.replace(/_/g, "[ _]"), "i"));
    return {
      mechanism: "tool",
      where: [
        { kind: "Tool description", ref: skipped, quote: desc },
        { kind: "System prompt", ref: section, quote },
      ],
      cause: `Nothing makes the agent call ${skipped} before it answers. ${desc ? `Its description only says “${desc.replace(/\.$/, "")}”` : "Its description doesn't say when it applies"}, and the ${section} section ${quote ? "mentions it without making it a step" : "never names it"}, so the agent answers from what the caller said.`,
      change: `Make ${skipped} a required step: its description says when it applies, and the ${section} section says the agent waits for it before answering.`,
      symptom,
    };
  }
  const section = sectionNamed(promptText, "Closing");
  const quote = lineMatching(promptText, /let the caller know|tell the caller|is on its way|confirm (it|that|the|what)|before (you )?(end|close)/i);
  const tool = spokePast || "the tool";
  return {
    mechanism: "claim",
    where: [
      { kind: "System prompt", ref: section, quote },
      ...(spokePast ? [{ kind: "Tool result", ref: spokePast, quote: null }] : []),
    ],
    cause: `The agent confirms before it reads ${spokePast ? `${spokePast}'s result` : "the tool's result"}. The ${section} section ${quote ? "tells it to close with that confirmation" : "ends the call with a confirmation"} and has no branch for a call that failed, so a failed ${tool === "the tool" ? "call" : tool} is still reported as done.`,
    change: `In the ${section} section, confirm only after ${tool} returns success. On an error, say what failed and offer another way.`,
    symptom,
  };
};
