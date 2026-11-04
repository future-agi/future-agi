import { useCallback } from "react";
import { useSnackbar } from "notistack";
import { rebuildForAgent, toolFit } from "../_mock/toolFit";
import { nextSnapshotLabel } from "../_mock/envSnapshot";
import { rebuildEnvironmentPatch, versionCardOf } from "../_mock/versionCard";
import { resolveNudgePatch } from "../_mock/decoupled";
import { pendingEnvChanges } from "../_mock/versions";
import { reviewCountsOf } from "../_mock/editReview";
import { openReviewChanges } from "./reviewChangesBus";

/**
 * Rebuild the environment for an agent version, and say what changed.
 *
 * One rebuild, whichever button asked for it (the tool banner, "Rebuild and
 * run", the agent's card): when the agent's change is on its card and the
 * card covers every tool the world is missing, the card's Rebuild runs — one
 * environment version, scenarios for new tools as its next step. Otherwise
 * the world learns the tools directly, and the card's adds it answered are
 * settled with it, so the card never offers the same rebuild twice. Either
 * way, scenarios for the new tools are proposed for Needs input, never added
 * straight into the environment.
 */
export function useRebuildForAgent(env, envState, patch) {
  const { enqueueSnackbar } = useSnackbar();
  return useCallback(
    (agentLabel) => {
      /* The rebuild makes the next environment version from the world as it
       stands: unsaved edits nobody has accepted or rejected would go into it
       unseen, so the review comes first (TH-8931). */
      if (reviewCountsOf(pendingEnvChanges(envState)).undecided) {
        openReviewChanges();
        enqueueSnackbar(
          "Accept or reject the unsaved edits first — the rebuild makes the next environment version, and they'd go into it. Then rebuild again.",
          { variant: "info" },
        );
        return null;
      }
      const shown = nextSnapshotLabel(env, envState);
      const card = versionCardOf(envState);
      const fitNow = toolFit(env, envState, { agent: agentLabel });
      const missing = fitNow.missing.map((t) => t.name);
      const reshaped = fitNow.changed.map((t) => t.name);
      const covered = new Set(
        (card?.capabilities || [])
          .filter((n) => n.kind === "add")
          .map((n) => n.capability),
      );
      if (
        card &&
        card.capabilities.length &&
        missing.every((t) => covered.has(t))
      ) {
        const out = rebuildEnvironmentPatch(env, envState, card);
        if (out) {
          patch(out.patch);
          enqueueSnackbar(
            `Environment ${shown} is ready — ${out.summary}. Scenarios for the new tools are the next step in Needs input.`,
            { variant: "success", autoHideDuration: 5000 },
          );
          return out.envLabel;
        }
      }
      const {
        version,
        added,
        patch: next,
      } = rebuildForAgent(env, envState, agentLabel);
      const learned = [...new Set(added.map((r) => r.newTool))];
      const answered = new Set(missing);
      let settled = { ...envState, ...next };
      (envState?.nudges || [])
        .filter(
          (n) =>
            n.kind === "add" &&
            answered.has(n.capability) &&
            (n.status === "open" || (n.status === "dismissed" && n.skippedAs)),
        )
        .forEach((n) => {
          settled = {
            ...settled,
            ...resolveNudgePatch(settled, n.id, "approved", {
              envVersion: version.label,
              rebuilt: true,
              skippedAs: undefined,
            }),
          };
        });
      patch({
        ...next,
        ...(settled.nudges !== envState?.nudges && { nudges: settled.nudges }),
      });
      enqueueSnackbar(
        learned.length
          ? `Environment ${shown} is ready — the world now answers ${learned.join(", ")}. ${added.length} scenarios that test ${learned.length === 1 ? "it" : "them"} are proposed in Needs input.`
          : reshaped.length
            ? `Environment ${shown} is ready — the world answers ${reshaped.join(", ")} with agent ${agentLabel}'s new arguments, so the blocked scenarios run again. Earlier runs keep their results.`
            : `Environment ${shown} is ready — re-derived against agent ${agentLabel}.`,
        { variant: "success", autoHideDuration: 5000 },
      );
      return version;
    },
    [env, envState, patch, enqueueSnackbar],
  );
}
