/*
  What Debug failures can do for a voice agent built outside Future AGI —
  in a voice platform's dashboard (Vapi, Retell…) or reached only by phone
  number (Nikhil, 2 Nov): we can't change it ourselves, so there is no
  improvement search or pull request.

    suggest   its prompt can be read (the platform exposes it, or it was
              pasted at connect) — diagnose and suggest the change to copy in
    diagnose  its prompt can't be read — diagnose only

  Every other agent (code, chat, prompt-in-Future-AGI) returns null and keeps
  the full Improve flow.
*/
import { PROVIDER_LABELS, definitionOf } from "./agentRegistry";

const VOICE_OUTSIDE = ["voice_platform", "voice_sip"];

export const voiceFixOf = (agent) => {
  if (!agent || !VOICE_OUTSIDE.includes(agent.typeId)) return null;
  const v = agent.values || {};
  const provider = PROVIDER_LABELS[v.provider] || null;
  const where =
    agent.typeId === "voice_sip"
      ? "your own phone system"
      : `${provider || "its platform"}'s dashboard`;
  const readable = !!(definitionOf(agent).prompt || v.systemPrompt);
  return { mode: readable ? "suggest" : "diagnose", where, provider };
};

/* The picked changes as plain text, to paste into the platform. */
export const changesTextOf = (proposals) =>
  proposals
    .map((p) =>
      [
        `• ${p.title}`,
        ...(p.diff || [])
          .filter((d) => d.type === "add")
          .map((d) => `  + ${d.text}`),
      ].join("\n"),
    )
    .join("\n\n");
