import { useEffect, useMemo } from "react";
import useFalconStore from "../store/useFalconStore";
import { planFor, slugFromMessage } from "../helpers/toolTrail";
import { getSkill } from "./useFalconAPI";

/**
 * The ordered tool flow a skill declared for the turn that produced this
 * assistant message, or an empty array when the turn ran no skill.
 *
 * The skill is read off the user message that triggered the turn, which is the
 * only per-turn record of it on the client. A turn with no leading slash
 * command, an unknown slug, or a skill that declares no trajectory returns
 * nothing and the trail falls back to its plain behaviour.
 */
export default function useSkillPlan(messageId, toolCalls) {
  const skills = useFalconStore((s) => s.skills);
  const skillPlans = useFalconStore((s) => s.skillPlans);
  const setSkillPlan = useFalconStore((s) => s.setSkillPlan);

  // A primitive keeps this subscription from re-rendering on every stream delta.
  const slug = useFalconStore((s) => {
    const at = s.messages.findIndex((m) => m.id === messageId);
    for (let i = at - 1; i >= 0; i -= 1) {
      if (s.messages[i].role === "user") {
        return slugFromMessage(s.messages[i].content);
      }
    }
    return null;
  });

  const skill = useMemo(
    () => (slug ? (skills || []).find((s) => s.slug === slug) : null) || null,
    [slug, skills],
  );

  const skillId = skill?.id;
  const declared = skill?.example_trajectories || skillPlans[skillId];

  useEffect(() => {
    if (!skillId || declared) return undefined;
    let live = true;
    getSkill(skillId)
      .then((data) => {
        if (!live) return;
        const result = data?.result || data;
        setSkillPlan(skillId, result?.example_trajectories || []);
      })
      // A miss is cached as "no flow" so a failing skill cannot refetch forever.
      .catch(() => {
        if (live) setSkillPlan(skillId, []);
      });
    return () => {
      live = false;
    };
  }, [skillId, declared, setSkillPlan]);

  return useMemo(
    () => planFor(declared || [], toolCalls),
    [declared, toolCalls],
  );
}
