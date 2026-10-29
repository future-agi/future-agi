import { useCallback } from "react";
import { useSimStore } from "../../store";
import { envAgentKey } from "../../_mock/agentRegistry";
import { skillCandidatesPatch, skillSetOf } from "../../_mock/skills";

/**
 * Save the skill changes a landed improvement made, as each skill's next
 * version on the agent (see skillCandidatesPatch). Returns the skill ids saved.
 */
export default function useSaveSkillCandidates() {
  const { state, dispatch } = useSimStore();
  return useCallback(
    (envId, envAgent, proposals, note) => {
      if (!envAgent) return [];
      const agent = {
        ...envAgent,
        key: envAgent.key || envAgentKey(envAgent, envId),
      };
      const set = skillSetOf(agent, state.workspaceSettings);
      const next = skillCandidatesPatch(set, proposals, { note });
      if (!next) return [];
      dispatch({
        type: "setWorkspaceSetting",
        key: "agentSkills",
        value: {
          ...(state.workspaceSettings?.agentSkills || {}),
          [agent.key]: {
            skills: next.skills,
            draft: next.draft || {},
            tests: next.tests || [],
          },
        },
      });
      return proposals.filter((p) => p.kind === "Skill").map((p) => p.skillId);
    },
    [state.workspaceSettings, dispatch],
  );
}
