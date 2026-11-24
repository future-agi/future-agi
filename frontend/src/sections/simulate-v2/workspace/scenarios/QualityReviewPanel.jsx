import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  InputBase,
  Stack,
  Tooltip,
  Typography,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { validate } from "../../_mock/contract";
import { asSentence } from "../../_mock/scenarioBrief";
import { StateChip, TextAction } from "./ScenarioStateBits";
import { scenarioTitle } from "../../_mock/scenarios";

/*
  Scenario quality review (PRD v7 §4.10.9).

  Whether the scenarios themselves can be trusted — not whether an agent may
  ship: the harness informs, the user decides. The strip sits on the
  Scenarios tab: the state, then the three facts it rests on — the quality
  gate, the human spot-check, rule coverage. The drawer is where the
  spot-check is done: a sample of scenarios, each marked "realistic and the
  checks are right" or "not right" with a note. Answers are kept per suite
  version; a new version carries forward the ones about scenarios that
  haven't changed.

  `gate` is suiteGateOf(env, envState).
*/

const NOTE = {
  "not-ready": "some scenarios need work",
  "spot-check": "a person needs to read a sample",
  ready: "scenarios read right and every rule is tested",
};

const chipState = (gate) => ({
  id: `gate-${gate.state}`,
  label: gate.label,
  note: NOTE[gate.state],
  why: gate.reason,
});

const pct = (x) => `${Math.round(x * 100)}%`;

const Sep = () => (
  <Box component="span" sx={{ color: "text.disabled" }}>
    {" · "}
  </Box>
);

/** The one-line status on the Scenarios tab. */
export function QualityReviewStrip({ gate, onOpen }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        mb: 1.5,
        px: 2,
        py: 1,
        borderRadius: "8px",
        flexWrap: "wrap",
        rowGap: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Iconify
        icon="solar:shield-check-linear"
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s2", fontWeight: 600, whiteSpace: "nowrap" }}
      >
        Quality review
      </Typography>
      <StateChip state={chipState(gate)} />
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          flex: 1,
          minWidth: 220,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {`${gate.admitted} of ${gate.total} pass the quality gate`}
        {gate.quarantined.length > 0 && (
          <>
            <Sep />
            <Box
              component="span"
              sx={{ color: "status.danger.fg" }}
            >{`${gate.quarantined.length} set aside`}</Box>
          </>
        )}
        <Sep />
        {`spot-check ${gate.answered} of ${gate.sampled}`}
        {gate.agreement != null && ` · ${pct(gate.agreement)} agree`}
        <Sep />
        {`${gate.coverage.covered} of ${gate.coverage.rules} rules covered`}
      </Typography>
      <Button
        variant="outlined"
        size="small"
        onClick={onOpen}
        sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
      >
        {gate.state === "spot-check" ? "Spot-check" : "Review"}
      </Button>
    </Stack>
  );
}
QualityReviewStrip.propTypes = {
  gate: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};

/* The strip's parts on their own, for the Scenarios toolbar's "Needs review"
   list: the state chip, and the three facts on one line. */
export function QualityReviewChip({ gate }) {
  return <StateChip state={chipState(gate)} />;
}
QualityReviewChip.propTypes = { gate: PropTypes.object.isRequired };

export function QualityReviewFacts({ gate }) {
  return (
    <Box component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {`${gate.admitted} of ${gate.total} pass the quality gate`}
      {gate.quarantined.length > 0 && (
        <>
          <Sep />
          <Box
            component="span"
            sx={{ color: "status.danger.fg" }}
          >{`${gate.quarantined.length} set aside`}</Box>
        </>
      )}
      <Sep />
      {`spot-check ${gate.answered} of ${gate.sampled}`}
      {gate.agreement != null && ` · ${pct(gate.agreement)} agree`}
      <Sep />
      {`${gate.coverage.covered} of ${gate.coverage.rules} rules covered`}
    </Box>
  );
}
QualityReviewFacts.propTypes = { gate: PropTypes.object.isRequired };

/*
  The drawer (rebuilt 24 Nov — the old one was three explained sections and
  ten fully open cards, the rule title repeated on each). Now:
    1. a three-row summary — each check and what's left of it;
    2. what to fix, only when there is something (an untested rule, a
       scenario the gate set aside);
    3. the spot-check, grouped by rule like the Scenarios table: one line per
       scenario with Right / Not right; what it asks opens on click.
*/
export default function QualityReviewPanel({
  gate,
  rows,
  env,
  connect = false,
  onAnswer,
  onOpenScenario,
  onClose,
  locked = false,
}) {
  const byId = new Map((rows || []).map((r) => [r.id, r]));
  const sample = gate.sample.map((id) => byId.get(id)).filter(Boolean);
  const groups = [];
  sample.forEach((row) => {
    const rule = scenarioTitle(row.title) || row.name;
    const g = groups.find((x) => x.rule === rule);
    if (g) g.rows.push(row);
    else groups.push({ rule, rows: [row] });
  });
  const left = gate.sampled - gate.answered;
  const uncovered = gate.coverage.uncovered;
  const summary = [
    {
      id: "gate",
      label: "Quality gate",
      tip: connect
        ? "Ready, solvable, not vacuous, mutation and exploit controls."
        : "Staged, solvable, not vacuous, alternate paths, mutation and exploit controls.",
      value: `${gate.admitted} of ${gate.total} pass`,
      todo: gate.quarantined.length
        ? `${gate.quarantined.length} set aside`
        : null,
    },
    {
      id: "spot",
      label: "Spot-check",
      tip: `A person reads a sample and says if each scenario is realistic and its checks are right. The bar is ${pct(gate.bar)} agreement.${gate.carried ? ` ${gate.carried} carried from ${gate.carriedFrom}, unchanged since.` : ""}`,
      value: `${gate.answered} of ${gate.sampled} read${gate.agreement != null ? ` · ${pct(gate.agreement)} agree` : ""}`,
      todo: left > 0 ? `${left} to read` : null,
    },
    {
      id: "rules",
      label: "Rules tested",
      tip: "Every rule the environment enforces needs a scenario that passes the quality gate testing it.",
      value: `${gate.coverage.covered} of ${gate.coverage.rules}`,
      todo: uncovered.length ? `${uncovered.length} untested` : null,
    },
  ];
  const fixes = uncovered.length + gate.quarantined.length;

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Quality review
            </Typography>
            <StateChip state={chipState(gate)} />
          </Stack>
          <Typography
            sx={{ typography: "s2", color: "text.tertiary", mt: 0.25 }}
          >
            Whether the scenarios can be trusted before you rely on run results.
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify
            icon="solar:close-circle-linear"
            width={20}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Stack>

      <Box sx={{ flex: 1, overflow: "auto", px: 2.5, py: 2 }}>
        {/* ── 1 · summary ── */}
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          {summary.map((s, i) => (
            <Stack
              key={s.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 1.5,
                py: 1,
                borderTop: i ? "1px solid" : 0,
                borderColor: "divider",
              }}
            >
              <Iconify
                icon={
                  s.todo
                    ? "solar:clock-circle-linear"
                    : "solar:check-circle-linear"
                }
                width={16}
                sx={{
                  flexShrink: 0,
                  color: s.todo ? "text.secondary" : "status.success.fg",
                }}
              />
              <Tooltip arrow placement="top-start" title={s.tip}>
                <Typography
                  sx={{
                    typography: "s2",
                    width: 110,
                    flexShrink: 0,
                    color: "text.secondary",
                    cursor: "help",
                  }}
                >
                  {s.label}
                </Typography>
              </Tooltip>
              <Typography
                sx={{
                  typography: "s2",
                  flex: 1,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {s.value}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: s.todo ? 500 : 400,
                  color: s.todo ? "text.primary" : "text.tertiary",
                  whiteSpace: "nowrap",
                }}
              >
                {s.todo || "Done"}
              </Typography>
            </Stack>
          ))}
        </Box>

        {/* ── 2 · what to fix, only when there is something ── */}
        {fixes > 0 && (
          <Box sx={{ mt: 2.5 }}>
            <SectionLabel>To fix</SectionLabel>
            <Stack
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
              }}
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
            >
              {uncovered.map((r) => (
                <Box key={r} sx={{ px: 1.5, py: 1 }}>
                  <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                    {asSentence(r)}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    No scenario tests this rule
                  </Typography>
                </Box>
              ))}
              {gate.quarantined.map((q) => (
                <Stack
                  key={q.id}
                  direction="row"
                  spacing={1.5}
                  alignItems="flex-start"
                  sx={{ px: 1.5, py: 1 }}
                >
                  <Box flex={1} minWidth={0}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 500 }}
                    >
                      {q.summary || scenarioTitle(q.title) || q.name}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >{`Set aside by the quality gate · ${q.reason}`}</Typography>
                  </Box>
                  {onOpenScenario && (
                    <TextAction onClick={() => onOpenScenario(q.id)}>
                      Open
                    </TextAction>
                  )}
                </Stack>
              ))}
            </Stack>
          </Box>
        )}

        {/* ── 3 · the spot-check ── */}
        <Box sx={{ mt: 2.5 }}>
          <SectionLabel right={`${gate.answered} of ${gate.sampled}`}>
            Spot-check · read each one: is it realistic, and are its checks
            right?
          </SectionLabel>
          {sample.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              No scenarios to sample yet.
            </Typography>
          ) : (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                overflow: "hidden",
              }}
            >
              {groups.map((g, gi) => (
                <Box key={g.rule}>
                  <Typography
                    sx={{
                      px: 1.5,
                      py: 0.75,
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.secondary",
                      bgcolor: "action.hover",
                      borderTop: gi ? "1px solid" : 0,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    {g.rule}
                    <Box
                      component="span"
                      sx={{ color: "text.disabled", fontWeight: 400, ml: 0.75 }}
                    >
                      {g.rows.length}
                    </Box>
                  </Typography>
                  {g.rows.map((row, ri) => (
                    <SpotCheckRow
                      key={row.id}
                      row={row}
                      env={env}
                      first={ri === 0}
                      answer={gate.answers[row.id] || null}
                      onAnswer={locked ? null : (a) => onAnswer(row.id, a)}
                    />
                  ))}
                </Box>
              ))}
            </Box>
          )}
        </Box>
      </Box>
    </Stack>
  );
}
QualityReviewPanel.propTypes = {
  gate: PropTypes.object.isRequired,
  rows: PropTypes.array,
  env: PropTypes.object,
  connect: PropTypes.bool,
  onAnswer: PropTypes.func,
  onOpenScenario: PropTypes.func,
  onClose: PropTypes.func,
  locked: PropTypes.bool,
};

function SectionLabel({ children, right }) {
  return (
    <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
      <Typography
        sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
      >
        {children}
      </Typography>
      {right && (
        <Typography
          sx={{
            typography: "s2",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {right}
        </Typography>
      )}
    </Stack>
  );
}
SectionLabel.propTypes = { children: PropTypes.node, right: PropTypes.string };

/*
  The spot-check answer: two small actions until it's answered, then the
  answer itself with Change. Not a segmented control — that reads as tabs.
  Stored ids stay "right" / "not-right".
*/
function Verdict({ answer, onPick }) {
  const v = answer?.verdict;
  if (v) {
    const good = v === "right";
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ flexShrink: 0 }}
      >
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Iconify
            icon={good ? "solar:check-circle-linear" : "solar:flag-linear"}
            width={14}
            sx={{ color: good ? "status.success.fg" : "status.warning.fg" }}
          />
          <Typography sx={{ typography: "s3", fontWeight: 500 }}>
            {good ? "Looks good" : "Flagged"}
          </Typography>
        </Stack>
        {onPick && <TextAction onClick={() => onPick(v)}>Change</TextAction>}
      </Stack>
    );
  }
  const tip = (t) => (onPick ? t : "Fork this environment to spot-check.");
  return (
    <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0 }}>
      <Tooltip
        arrow
        title={tip("Realistic, and its checks would grade it correctly")}
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled={!onPick}
            onClick={() => onPick("right")}
            sx={{
              height: 24,
              px: 1,
              minWidth: 0,
              typography: "s3",
              color: "text.primary",
              borderColor: "divider",
            }}
          >
            Looks good
          </Button>
        </span>
      </Tooltip>
      <Tooltip
        arrow
        title={tip("Unrealistic, or a check is wrong — you'll say what")}
      >
        <span>
          <Button
            size="small"
            variant="text"
            disabled={!onPick}
            onClick={() => onPick("not-right")}
            startIcon={<Iconify icon="solar:flag-linear" width={14} />}
            sx={{
              height: 24,
              px: 1,
              minWidth: 0,
              typography: "s3",
              color: "text.secondary",
              "& .MuiButton-startIcon": { mr: 0.5 },
            }}
          >
            Flag
          </Button>
        </span>
      </Tooltip>
    </Stack>
  );
}
Verdict.propTypes = { answer: PropTypes.object, onPick: PropTypes.func };

/* One sampled scenario: one line and the verdict; what it asks on click. */
function SpotCheckRow({ row, env, answer, onAnswer, first }) {
  const s = validate(row, env);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(answer?.note || "");
  useEffect(() => {
    setNote(answer?.note || "");
  }, [answer?.note]);
  const checks = (s?.checks || []).map((c) => {
    const t = c.label.toLowerCase().replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  });
  const pick = (verdict) => {
    if (!onAnswer) return;
    if (answer?.verdict === verdict) onAnswer({ verdict: null });
    else onAnswer({ verdict, note: verdict === "not-right" ? note : "" });
  };
  const name = row.summary || scenarioTitle(row.title) || row.name;
  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.75,
        borderTop: first ? 0 : "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <ButtonBase
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          sx={{
            flex: 1,
            minWidth: 0,
            justifyContent: "flex-start",
            textAlign: "left",
            gap: 0.75,
            py: 0.25,
            borderRadius: "4px",
          }}
        >
          <Iconify
            icon={
              open
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <Typography noWrap sx={{ typography: "s2", minWidth: 0 }}>
            {name}
          </Typography>
        </ButtonBase>
        <Verdict answer={answer} onPick={onAnswer ? pick : null} />
      </Stack>
      {open && (
        <Box
          sx={{
            mt: 0.5,
            mb: 0.5,
            ml: 2.75,
            display: "grid",
            gridTemplateColumns: "84px minmax(0, 1fr)",
            columnGap: 1.5,
            rowGap: 0.25,
            "& .sc-l": {
              typography: "s3",
              color: "text.tertiary",
              lineHeight: 1.55,
            },
            "& .sc-v": {
              typography: "s3",
              color: "text.secondary",
              lineHeight: 1.55,
            },
          }}
        >
          {row.task && (
            <>
              <Typography className="sc-l">Asks</Typography>
              <Typography className="sc-v">
                {String(row.task).replace(/\.{2,}$/, ".")}
              </Typography>
            </>
          )}
          {(row.expected || row.idealOutcome) && (
            <>
              <Typography className="sc-l">Passes when</Typography>
              <Typography className="sc-v">
                {asSentence(row.expected || row.idealOutcome)}
              </Typography>
            </>
          )}
          {checks.length > 0 && (
            <>
              <Typography className="sc-l">Graded on</Typography>
              <Typography className="sc-v">{checks.join(" · ")}</Typography>
            </>
          )}
        </Box>
      )}
      {answer?.verdict === "not-right" && (
        <InputBase
          fullWidth
          multiline
          value={note}
          disabled={!onAnswer}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => {
            if (onAnswer && note !== (answer.note || ""))
              onAnswer({ verdict: "not-right", note });
          }}
          placeholder="What's off — an unrealistic caller, a check that's wrong, a missing step…"
          sx={{
            mt: 0.75,
            mb: 0.25,
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            typography: "s2",
          }}
        />
      )}
      {answer?.carriedFrom && (
        <Typography sx={{ typography: "s3", color: "text.disabled", ml: 2.75 }}>
          {`Answered on ${answer.carriedFrom}${answer.by ? ` by ${answer.by}` : ""} — unchanged since`}
        </Typography>
      )}
    </Box>
  );
}
SpotCheckRow.propTypes = {
  row: PropTypes.object,
  env: PropTypes.object,
  answer: PropTypes.object,
  onAnswer: PropTypes.func,
  first: PropTypes.bool,
};
