import PropTypes from "prop-types";
import { Fragment, useEffect, useState } from "react";
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

/** The drawer: the quality gate, the spot-check, coverage. */
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
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Quality review
            </Typography>
            <StateChip state={chipState(gate)} />
          </Stack>
          <Typography
            sx={{ typography: "s2", color: "text.tertiary", mt: 0.25 }}
          >
            How far the scenarios can be trusted: each passes the quality gate
            before it joins the set, a person agrees a sample is realistic and
            checked right, and every rule is tested.
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

      <Box sx={{ flex: 1, overflow: "auto" }}>
        <Typography
          sx={{
            px: 2.5,
            pt: 1.75,
            pb: 0.25,
            typography: "s2",
            color: gate.state === "ready" ? "text.primary" : "text.secondary",
          }}
        >
          {gate.reason}
        </Typography>

        {/* ── the quality gate ── */}
        <Part
          title="Quality gate"
          figure={`${gate.admitted} of ${gate.total}`}
          sub={
            connect
              ? "Ready, solvable, not vacuous (or positive evidence where declining is correct), mutation controls and exploit control — the world-only gates don't apply without a world."
              : "Staged, solvable, not vacuous (or positive evidence where declining is correct), mutation controls, alternate valid paths, setup can't earn credit, and exploit control."
          }
        >
          {gate.quarantined.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Every scenario in the set passes.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {gate.quarantined.map((q) => (
                <Stack
                  key={q.id}
                  direction="row"
                  spacing={1}
                  alignItems="flex-start"
                >
                  <Iconify
                    icon="solar:shield-cross-linear"
                    width={14}
                    sx={{ color: "status.danger.fg", flexShrink: 0, mt: "3px" }}
                  />
                  <Box flex={1} minWidth={0}>
                    {/* Title first, like the Scenarios table; the slug only when there's no title. */}
                    <Typography
                      noWrap
                      sx={{ typography: "s2_1", fontWeight: 500 }}
                    >
                      {scenarioTitle(q.title) || q.name}
                    </Typography>
                    {q.summary && (
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {q.summary}
                      </Typography>
                    )}
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        lineHeight: 1.55,
                      }}
                    >
                      {q.reason}
                    </Typography>
                  </Box>
                  {onOpenScenario && (
                    <TextAction onClick={() => onOpenScenario(q.id)}>
                      Open
                    </TextAction>
                  )}
                </Stack>
              ))}
            </Stack>
          )}
        </Part>

        {/* ── the human spot-check ── */}
        <Part
          title="Human spot-check"
          figure={
            gate.agreement == null
              ? `0 of ${gate.sampled}`
              : `${pct(gate.agreement)} agree`
          }
          sub={`${gate.answered} of ${gate.sampled} checked · the bar is ${pct(gate.bar)} agreement over the whole sample${gate.carried ? ` · ${gate.carried} carried from ${gate.carriedFrom}, unchanged since` : ""}.`}
        >
          {sample.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              No scenarios to sample yet.
            </Typography>
          ) : (
            <Stack
              divider={
                <Box
                  sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                />
              }
              sx={{ mx: -2.5 }}
            >
              {sample.map((row) => (
                <SpotCheckRow
                  key={row.id}
                  row={row}
                  env={env}
                  answer={gate.answers[row.id] || null}
                  onAnswer={locked ? null : (a) => onAnswer(row.id, a)}
                />
              ))}
            </Stack>
          )}
        </Part>

        {/* ── coverage ── */}
        <Part
          title="Coverage"
          figure={`${gate.coverage.covered} of ${gate.coverage.rules} rules`}
          sub="Every rule the environment enforces needs a scenario that passes the quality gate testing it."
          last
        >
          {gate.coverage.uncovered.length === 0 ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Every rule is tested.
            </Typography>
          ) : (
            <Stack spacing={0.5}>
              {gate.coverage.uncovered.map((r) => (
                <Typography
                  key={r}
                  sx={{ typography: "s2", color: "text.secondary" }}
                >{`No scenario tests "${r}"`}</Typography>
              ))}
            </Stack>
          )}
        </Part>
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

function Part({ title, figure, sub, children, last = false }) {
  return (
    <Box
      sx={{
        px: 2.5,
        py: 2,
        borderBottom: last ? 0 : "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography sx={{ typography: "s1", fontWeight: 600, flex: 1 }}>
          {title}
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 600,
            color: "text.primary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {figure}
        </Typography>
      </Stack>
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", mt: 0.25, mb: 1.25 }}
      >
        {sub}
      </Typography>
      {children}
    </Box>
  );
}
Part.propTypes = {
  title: PropTypes.string,
  figure: PropTypes.string,
  sub: PropTypes.string,
  children: PropTypes.node,
  last: PropTypes.bool,
};

const VERDICTS = [
  { id: "right", label: "Right", tip: "Realistic, and its checks are right" },
  {
    id: "not-right",
    label: "Not right",
    tip: "Unrealistic, or a check is wrong — say what",
  },
];

/* One sampled scenario: what it asks, what passes it, and the verdict. */
function SpotCheckRow({ row, env, answer, onAnswer }) {
  const s = validate(row, env);
  const [note, setNote] = useState(answer?.note || "");
  useEffect(() => {
    setNote(answer?.note || "");
  }, [answer?.note]);
  const checks = (s?.checks || []).map((c) =>
    c.label.toLowerCase().replace(/_/g, " "),
  );
  const pick = (verdict) => {
    if (!onAnswer) return;
    if (answer?.verdict === verdict) onAnswer({ verdict: null });
    else onAnswer({ verdict, note: verdict === "not-right" ? note : "" });
  };
  return (
    <Box sx={{ px: 2.5, py: 1.25 }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {scenarioTitle(row.title) || row.name}
          </Typography>
          {/* The caller variation — never the title a second time. */}
          {row.summary && row.summary !== row.title && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {row.summary}
            </Typography>
          )}
        </Box>
        <Stack
          direction="row"
          sx={{
            flexShrink: 0,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            p: "1px",
          }}
        >
          {VERDICTS.map((v) => {
            const on = answer?.verdict === v.id;
            return (
              <Tooltip
                key={v.id}
                arrow
                title={
                  onAnswer ? v.tip : "Fork this environment to spot-check."
                }
              >
                <ButtonBase
                  type="button"
                  aria-pressed={on}
                  disabled={!onAnswer}
                  onClick={() => pick(v.id)}
                  sx={{
                    p: 0,
                    px: 1,
                    height: 22,
                    border: 0,
                    borderRadius: "4px",
                    font: "inherit",
                    typography: "s3",
                    fontWeight: on ? 600 : 500,
                    cursor: onAnswer ? "pointer" : "default",
                    color: on ? "text.primary" : "text.tertiary",
                    bgcolor: on ? "action.selected" : "transparent",
                    "&:hover":
                      onAnswer && !on ? { color: "text.primary" } : undefined,
                  }}
                >
                  {v.label}
                </ButtonBase>
              </Tooltip>
            );
          })}
        </Stack>
      </Stack>
      <Box
        sx={{
          mt: 0.75,
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
            <Typography
              className="sc-v"
              sx={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {row.task}
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
            <Typography className="sc-v">
              {checks.map((c, i) => (
                <Fragment key={c}>
                  {i > 0 && <Sep />}
                  {c.charAt(0).toUpperCase() + c.slice(1)}
                </Fragment>
              ))}
            </Typography>
          </>
        )}
      </Box>
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
          placeholder="What's wrong — an unrealistic caller, a check that's off, a missing step…"
          sx={{
            mt: 1,
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
        <Typography sx={{ typography: "s3", color: "text.disabled", mt: 0.5 }}>
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
};
