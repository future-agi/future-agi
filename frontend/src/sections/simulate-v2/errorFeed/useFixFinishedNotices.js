/*
  A fix runs for minutes to hours, in the background — nobody should have to
  keep checking the Error Feed to learn it's done. When an improvement
  reaches its ending, it says so once: an in-app notice with a way to it, a
  browser notification if they've already allowed them (never a permission
  prompt out of nowhere), and Slack if the workspace routes "Fix finished"
  there. Stamped on the record (finishNotifiedAt), so it's said once
  whichever page is open; one that finished long before anyone looked is
  stamped quietly rather than announced late.
*/
import { createElement, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import { Button } from "@mui/material";
import { improvementOutcome } from "../_mock/optimizationRuns";
import { improvementRunName } from "../_mock/optimizer";
import { postSlack } from "../_mock/integrations";
import { improvementPath } from "../improvements/improvementNav";

const TICK_MS = 10000;
/* Older than this when first seen finished: stamped, not announced. */
const ANNOUNCE_WITHIN_MS = 3 * 60 * 60 * 1000;

const SAID = {
  ready: "is ready for review",
  "stopped-early": "stopped early — its best change is ready for review",
  budget: "stopped at its budget — its best change so far is ready for review",
  suggestion: "has a suggested change for you to review",
  "no-fix": "found no safe change — it says what to try next",
  "not-reproduced": "couldn't make the issue happen — it says what to try next",
  failed: "couldn't finish for infrastructure reasons",
};

export default function useFixFinishedNotices(state, commit) {
  const navigate = useNavigate();
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const ref = useRef(state);
  ref.current = state;
  const hydrated = !!state?.hydrated;

  useEffect(() => {
    if (!hydrated) return undefined;
    const tick = () => {
      const s = ref.current;
      const now = Date.now();
      (s?.myEnvironments || []).forEach((env) => {
        const es = s.byEnv?.[env.id];
        const list = es?.optimizations || [];
        const done = [];
        list.forEach((o) => {
          if (o.finishNotifiedAt || o.status === "draft") return;
          let out = null;
          try {
            out = improvementOutcome(o);
          } catch (e) {
            return;
          }
          const key = out?.ending?.key;
          if (!key || key === "running" || !(key in SAID)) return;
          const since = Date.parse(o.createdAt || o.startedAt || "") || 0;
          done.push({ o, key, announce: now - since < ANNOUNCE_WITHIN_MS });
        });
        if (!done.length) return;
        const at = new Date(now).toISOString();
        const stamped = new Set(done.map((d) => d.o.id));
        commit({
          type: "patchEnvState",
          envId: env.id,
          patch: {
            optimizations: list.map((o) =>
              stamped.has(o.id) ? { ...o, finishNotifiedAt: at } : o,
            ),
          },
        });
        done
          .filter((d) => d.announce)
          .forEach(({ o, key }) => {
            const issueId = o.trigger?.issueId;
            const name = issueId
              ? `The fix for ${issueId}`
              : improvementRunName(es, o);
            const title = `${name} ${SAID[key]}`;
            const href = improvementPath(env.id, o.id);
            const open = () => navigate(href);
            enqueueSnackbar(title, {
              variant: key === "ready" ? "success" : "info",
              autoHideDuration: 8000,
              action: (snack) =>
                createElement(
                  Button,
                  {
                    size: "small",
                    color: "inherit",
                    onClick: () => {
                      closeSnackbar(snack);
                      open();
                    },
                  },
                  key === "ready" || key === "stopped-early" || key === "budget"
                    ? "Review"
                    : "Open",
                ),
            });
            try {
              if (
                typeof window !== "undefined" &&
                "Notification" in window &&
                window.Notification.permission === "granted"
              ) {
                const note = new window.Notification(title, {
                  body: env.name,
                  tag: `fix-finished-${o.id}`,
                });
                note.onclick = () => {
                  window.focus();
                  open();
                  note.close();
                };
              }
            } catch (e) {
              /* notifications unavailable */
            }
            postSlack({
              event: "fix_finished",
              title,
              lines: [env.name],
              actions: ["Open fix"],
              links: { "Open fix": href },
            });
          });
      });
    };
    tick();
    const t = setInterval(tick, TICK_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);
}
