import { useCallback } from "react";
import { useSimStore } from "../../store";
import { envAgentKey } from "../../_mock/agentRegistry";
import { latestOf, skillCandidatesPatch, skillSetOf } from "../../_mock/skills";
import { attachmentsOf, libraryOf, newVersion } from "../../_mock/skillLibrary";

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
      /* A library skill attached to this agent: the change is its next
         library version — the one before stays as it was (skills.md SK-2).
         A repo skill still changes only through the pull request. */
      const attachedIds = new Set(
        (attachmentsOf(state.workspaceSettings)[agent.key] || []).map(
          (a) => a.skillId,
        ),
      );
      let settings = state.workspaceSettings;
      proposals
        .filter((p) => p.kind === "Skill" && p.skillId)
        .forEach((p) => {
          const lib = libraryOf(settings).find(
            (x) => x.name === p.skillId && attachedIds.has(x.id),
          );
          const changed = next.skills.find((x) => x.id === p.skillId);
          if (!lib || !changed) return;
          const text =
            latestOf(changed).files?.find((f) => f.path === "SKILL.md")?.text ||
            "";
          const out = newVersion(settings, lib.id, {
            text,
            note: note || "From an improvement",
          });
          settings = { ...settings, ...out };
          dispatch({
            type: "setWorkspaceSetting",
            key: "skillLibrary",
            value: out.skillLibrary,
          });
        });
      return proposals.filter((p) => p.kind === "Skill").map((p) => p.skillId);
    },
    [state.workspaceSettings, dispatch],
  );
}
