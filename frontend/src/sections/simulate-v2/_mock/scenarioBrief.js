/**
 * Reading a scenario's brief — the text the simulated caller is given.
 *
 * In production it arrives as one paragraph: who the caller is, then what to
 * do turn by turn ("When the agent answers, ask… If the agent explains…,
 * follow up…"), then an inline list ("Your details: - Name: … - Company
 * role: …"). As a single grey block it is hard to read. Split, it is an
 * intro, a sequence of moves, and a small table of facts.
 */

/* Sentences that direct the caller's next move. */
const STEP_START = /^(when|if|once|then|after|afterwards|finally|next|first|start|begin|ask|tell|say|follow)\b/i;

const splitSentences = (text) => text
  .split(/(?<=[.!?])\s+(?=[A-Z"“])/)
  .map((s) => s.trim())
  .filter(Boolean);

export function parseBrief(text = "") {
  let body = String(text || "").trim();

  /* "Your details: - Name: Marcus Vance - Company role: … - Inquiry: …" */
  let details = [];
  const at = body.search(/your details:/i);
  if (at >= 0) {
    const tail = body.slice(at).replace(/^your details:\s*/i, "");
    body = body.slice(0, at).trim();
    details = tail
      .split(/\s*-\s+(?=[A-Z][A-Za-z /&]{0,32}:)/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => {
        const i = item.indexOf(":");
        return i > 0
          ? { key: item.slice(0, i).trim(), value: item.slice(i + 1).trim().replace(/\.$/, "") }
          : { key: null, value: item.replace(/\.$/, "") };
      })
      .filter((d) => d.value);
  }

  /* Who they are and why they're calling, then what they do, in order. */
  const intro = [];
  const steps = [];
  splitSentences(body).forEach((sentence) => {
    if (steps.length || STEP_START.test(sentence)) steps.push(sentence);
    else intro.push(sentence);
  });

  return { intro: intro.join(" "), steps, details };
}

/** A sub-goal's text, whatever shape it came in. */
export const subGoalText = (g) => {
  if (typeof g === "string") return g;
  if (!g || typeof g !== "object") return "";
  return g.label || g.text || g.description || g.goal || g.title || g.name || "";
};

/** "the agent explains…" → "The agent explains…." */
export const asSentence = (text = "") => {
  const t = String(text || "").trim();
  if (!t) return "";
  const cap = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?"”]$/.test(cap) ? cap : `${cap}.`;
};
