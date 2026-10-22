import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, ButtonBase, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { improvementOutcome, ladderOf } from "../_mock/optimizationRuns";
import { improvementRunName } from "../_mock/optimizer";
import { shippedCandidate } from "../run/fixmyagent/tradeoffs";
import StatusPill from "./ImprovementStatusPill";
import { agentDefinitionOf } from "../_mock/agentRegistry";

const GREEN = "#16A34A";
const RED = "#DC2626";
const num = { fontVariantNumeric: "tabular-nums" };
/* Section heads read like the rest of the product's record views. */
const head = { typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, mb: 1 };
const signed = (n, unit) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(Math.round(n * 10) / 10)}${unit}`;

/**
 * How an improvement was decided — a short record, read like a spec sheet:
 * the result, the evidence it stands on, the finalists, the checks and the
 * changes. Colour only marks a change for better or worse, as in the runs
 * table. The search, per-scenario outcomes, trade-offs and the receipt are
 * in the full record.
 */
export default function ImprovementDecision({ record, env, envState, onClose, onFullRecord }) {
  const result = record.result;
  /* The model the agent runs on — what a model lock is checked against, as
     on the runs page, so the two read the same constraint the same way. */
  const agentModel = useMemo(() => {
    if (!envState?.agent) return null;
    const def = agentDefinitionOf({ agent: envState.agent, env, envState });
    return def.model?.visible ? def.model.name : null;
  }, [env, envState]);
  const outcome = improvementOutcome(record, { model: agentModel });
  const winner = result?.winner || null;
  const shipped = shippedCandidate(record) || winner;
  const name = improvementRunName(envState, record);
  const chosen = !!shipped?.chosen;

  const finalists = [...(result?.finalists || [])].sort((a, b) => (b.winner ? 1 : 0) - (a.winner ? 1 : 0) || (b.heldScore ?? 0) - (a.heldScore ?? 0));
  const tiers = shipped ? ladderOf(record, shipped) : [];
  const checksPassed = tiers.filter((t) => t.state === "pass").length;
  const failedAt = tiers.find((t) => t.state === "fail");
  const limits = outcome.checks || [];
  const broken = limits.filter((c) => c.pass === false);
  const measured = limits.filter((c) => c.pass !== null).length;
  const thin = !result || result.split?.thin;
  /* A chosen run's numbers are its own; its held-back figure is projected
     until it's verified. */
  const heldNow = outcome.heldScore ?? result?.heldScore;
  const projected = !!outcome.chosen?.projected;
  const unverified = !!outcome.chosen && !outcome.chosen.verified;
  const confirm = result?.confirm || null;

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>How it was decided</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {`${name}${shipped ? ` · Run ${shipped.n} ${chosen ? `chosen by you${unverified ? " · not verified yet" : " · verified"}` : "kept"}` : ""}`}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify icon="mingcute:close-line" width={18} sx={{ color: "text.secondary" }} />
        </IconButton>
      </Stack>

      <Stack spacing={3} sx={{ px: 2.5, py: 2.5, overflowY: "auto", flex: 1 }}>
        {/* result */}
        <Box>
          <Box sx={{ mb: 1 }}><StatusPill ending={outcome.ending} /></Box>
          <Typography sx={{ typography: "s1", fontWeight: 600, lineHeight: 1.5 }}>{outcome.headline}</Typography>
        </Box>

        {/* evidence */}
        {shipped && (
          <Box>
            <Typography sx={head}>Evidence</Typography>
            <Sheet>
              {outcome.strength && (
                <Line k="Strength" hint="How much the held-back result is worth: proven, likely, or too few scenarios to tell." v={outcome.strength.label} sub={`${outcome.strength.note.charAt(0).toUpperCase()}${outcome.strength.note.slice(1)}.`} />
              )}
              {confirm && !projected && (
                <Line
                  k="Held back · at the pick"
                  hint="The held-back run the finalists were ranked on. It picks the winner, so it can't also prove it."
                  v={`${result.heldBase}% → ${confirm.selectionScore}%`}
                  d={{ pts: confirm.selectionScore - result.heldBase, quiet: true }}
                />
              )}
              <Line
                k={confirm && !projected ? "Held back · re-run" : "Held-back scenarios"}
                hint={projected ? "Projected from the search's pick — this run hasn't been scored on the held-back set yet. Verify it to measure it." : confirm ? "The winner ran on the held-back set again after it was picked — the number worth quoting." : "Scenarios kept out of the search — the number worth quoting."}
                v={thin ? "Too few held back" : `${result.heldBase}% → ${heldNow}%${projected ? " · projected" : ""}`}
                d={thin ? null : { pts: heldNow - result.heldBase }}
              />
              <Line k="Target scenarios" hint="The scenarios the search tuned on." v={`${result.base}% → ${shipped.score}%`} d={{ pts: shipped.score - result.base, quiet: true }} />
              <Line
                k="Overfit gap"
                hint="Target score minus held-back score — how much the change fitted the scenarios it was tuned on rather than the task."
                v={thin || outcome.gap == null ? "—" : `${outcome.gap > 0 ? "" : outcome.gap < 0 ? "−" : ""}${Math.abs(outcome.gap)} pts${projected ? " · projected" : ""}`}
                d={{ alarm: !thin && outcome.gap > 12 }}
              />
              <Line k="Cost / conversation" v={outcome.costKnown ? signed(outcome.effect.cost, "%") : "Not measured"} />
              <Line k="Latency" v={signed(outcome.effect.latency, "%")} />
              <Line k="Target scenarios broken" v={`${shipped.broke ?? 0}`} d={{ alarm: !!shipped.broke }} last />
            </Sheet>
          </Box>
        )}

        {/* finalists */}
        {finalists.length > 0 && (
          <Box>
            <Typography sx={head}>Finalists</Typography>
            <Sheet>
              <Stack direction="row" sx={{ px: 1.5, py: 0.75, borderBottom: "1px solid", borderColor: "divider" }}>
                <Typography sx={{ ...cell, flex: 1, color: "text.subtitle", fontWeight: 600 }}>Run</Typography>
                <Typography sx={{ ...cell, width: 84, textAlign: "right", color: "text.subtitle", fontWeight: 600 }}>Target</Typography>
                <Typography sx={{ ...cell, width: 84, textAlign: "right", color: "text.subtitle", fontWeight: 600 }}>Held back</Typography>
                <Typography sx={{ ...cell, width: 120, pl: 2, color: "text.subtitle", fontWeight: 600 }}>Result</Typography>
              </Stack>
              {finalists.map((f, i) => (
                <Stack key={f.n} direction="row" alignItems="center" sx={{ px: 1.5, py: 0.875, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                  <Typography sx={{ ...cell, flex: 1, fontWeight: f.winner ? 700 : 500 }}>{`Run ${f.n}`}</Typography>
                  <Typography sx={{ ...cell, width: 84, textAlign: "right", ...num }}>{`${f.score}%`}</Typography>
                  <Typography sx={{ ...cell, width: 84, textAlign: "right", fontWeight: 600, ...num }}>{f.heldScore == null ? "—" : `${f.heldScore}%`}</Typography>
                  <Tooltip arrow title={f.why || ""} disableHoverListener={!f.why || f.accepted}>
                    <Typography noWrap sx={{ ...cell, width: 120, pl: 2, color: f.winner ? "text.primary" : f.accepted ? "text.secondary" : RED, fontWeight: f.winner ? 600 : 400 }}>
                      {f.winner ? (chosen ? "Search's pick" : "Kept") : f.n === shipped?.n ? "Your pick" : f.accepted ? "Passed" : "Didn't pass"}
                    </Typography>
                  </Tooltip>
                </Stack>
              ))}
            </Sheet>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
              {chosen
                ? unverified
                  ? `Run ${shipped.n} was chosen over the search's pick. It can't ship until it's verified on the full suite${projected ? " — its held-back result is projected until then" : ""}.`
                  : `Run ${shipped.n} was chosen over the search's pick and verified on the full suite.`
                : confirm
                  ? `Finalists are ranked on one held-back run; of those that pass, the highest target score is kept. Run ${shipped.n} then ran on the held-back set again — ${confirm.held ? "it held" : "it didn't hold"}.`
                  : "Finalists are scored once on held-back scenarios; of those that pass, the highest target score is kept."}
            </Typography>
          </Box>
        )}

        {/* checks */}
        {shipped && (limits.length > 0 || tiers.length > 0) && (
          <Box>
            <Typography sx={head}>Checks</Typography>
            <Sheet>
              {limits.length > 0 && (
                <Line
                  k="Agent constraints"
                  v={broken.length ? `${broken.length} broken` : `${measured} of ${measured} held`}
                  d={{ alarm: broken.length > 0 }}
                  hint={broken.length ? broken.map((c) => c.label).join(", ") : undefined}
                  last={!tiers.length}
                />
              )}
              {tiers.length > 0 && (
                <Line k="Verification" v={failedAt ? `Stopped at ${failedAt.label}` : `${checksPassed} of ${tiers.length} passed`} d={{ alarm: !!failedAt }} last />
              )}
            </Sheet>
          </Box>
        )}

        {/* changes */}
        {(shipped?.proposals || []).length > 0 && (
          <Box>
            <Typography sx={head}>{`Changes · ${shipped.proposals.length}`}</Typography>
            <Sheet>
              {shipped.proposals.map((p, i) => {
                const added = (p.diff || []).find((d) => d.type === "add")?.text;
                return (
                  <Box key={p.id} sx={{ px: 1.5, py: 1.125, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                    <Stack direction="row" alignItems="baseline" spacing={1}>
                      <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}>{p.title}</Typography>
                      {p.kind && <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>{p.kind}</Typography>}
                    </Stack>
                    {added && (
                      <Typography noWrap sx={{ mt: 0.375, fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12, color: "text.subtitle" }}>
                        {`+ ${added}`}
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Sheet>
          </Box>
        )}
      </Stack>

      <Box sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
        <ButtonBase onClick={onFullRecord} sx={{ gap: 0.5, typography: "s2", fontWeight: 600, color: "text.secondary", borderRadius: 0.75, "&:hover": { color: "text.primary" } }}>
          Full record — the search, per scenario, trade-offs, receipt
          <Iconify icon="solar:arrow-right-linear" width={14} />
        </ButtonBase>
      </Box>
    </Stack>
  );
}

ImprovementDecision.propTypes = {
  record: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onFullRecord: PropTypes.func.isRequired,
};

const cell = { typography: "s2" };

function Sheet({ children }) {
  return <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>{children}</Box>;
}
Sheet.propTypes = { children: PropTypes.node };

/*
  One fact: label, value and — when it moved — the change, coloured for
  better or worse as the runs table colours its deltas.
*/
function Line({ k, v, sub, d, hint, last }) {
  const pts = d?.pts;
  /* Only a points change is coloured; a value turns red only when something broke. */
  const tone = d?.quiet ? "text.subtitle" : pts < 0 ? RED : GREEN;
  return (
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.5, py: 0.875, ...(!last && { borderBottom: "1px solid", borderColor: "divider" }) }}>
      <Stack direction="row" alignItems="center" spacing={0.5} sx={{ width: 160, flexShrink: 0 }}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{k}</Typography>
        {hint && (
          <Tooltip arrow title={hint}>
            <Box component="span" sx={{ display: "flex" }}><Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.disabled" }} /></Box>
          </Tooltip>
        )}
      </Stack>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, ...num, color: d?.alarm ? RED : "text.primary" }}>{v}</Typography>
        {sub && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>}
      </Box>
      {pts != null && pts !== 0 && (
        <Typography sx={{ typography: "s3", fontWeight: 600, color: tone, ...num }}>{`${pts > 0 ? "↑" : "↓"} ${Math.abs(pts)} pts`}</Typography>
      )}
    </Stack>
  );
}
Line.propTypes = { k: PropTypes.string, v: PropTypes.node, sub: PropTypes.string, d: PropTypes.object, hint: PropTypes.string, last: PropTypes.bool };
