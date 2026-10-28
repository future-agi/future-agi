import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_SPOT_CHECK,
  sampleAnswers,
  sampleGateOf,
} from "src/api/simulate-environments/_fixtures/detail/scenarioStates";
import SideDrawer from "../../components/SideDrawer";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";

const NOTE = {
  "not-ready": "some scenarios need work",
  "spot-check": "a person needs to read a sample",
  ready: "scenarios read right and every rule is tested",
};

const pct = (x) => `${Math.round(x * 100)}%`;

const Sep = () => (
  <Box component="span" sx={{ color: "text.disabled" }}>
    {" · "}
  </Box>
);

// The gate's state as Vel's StateChip draws it: neutral, the note on hover.
export function QualityStateChip({ gate }) {
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={`${gate.label}: ${NOTE[gate.state]}. ${gate.reason}`}
    >
      <Box
        component="span"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          flexShrink: 0,
          height: 22,
          px: 0.875,
          borderRadius: "4px",
          border: "1px solid",
          borderColor: "divider",
          color: "text.secondary",
          typography: "s3",
          fontWeight: 600,
          whiteSpace: "nowrap",
        }}
      >
        {gate.label}
      </Box>
    </CustomTooltip>
  );
}
QualityStateChip.propTypes = { gate: PropTypes.object.isRequired };

// The three facts the gate rests on, on one line.
export function QualityFacts({ gate }) {
  return (
    <Box component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {`${gate.admitted} of ${gate.total} pass the quality gate`}
      {gate.quarantined.length > 0 && (
        <>
          <Sep />
          <Box component="span" sx={{ color: "error.main" }}>
            {`${gate.quarantined.length} set aside`}
          </Box>
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
QualityFacts.propTypes = { gate: PropTypes.object.isRequired };

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
        sx={{ typography: "s3", color: "text.subtitle", mt: 0.25, mb: 1.25 }}
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
    tip: "Unrealistic, or a check is wrong. Say what.",
  },
];

// One sampled scenario: what it asks, what passes it, and the verdict.
function SpotCheckRow({ row, answer, onAnswer }) {
  const [note, setNote] = useState(answer?.note || "");
  useEffect(() => {
    setNote(answer?.note || "");
  }, [answer?.note]);
  const pick = (verdict) => {
    if (answer?.verdict === verdict) onAnswer({ verdict: null });
    else onAnswer({ verdict, note: verdict === "not-right" ? note : "" });
  };
  return (
    <Box sx={{ px: 2.5, py: 1.25 }} data-testid="spot-check-row">
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {row.title}
          </Typography>
          {row.summary && row.summary !== row.title && (
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle" }}
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
              <CustomTooltip
                key={v.id}
                show
                arrow
                describeChild
                size="small"
                title={v.tip}
              >
                <ButtonBase
                  type="button"
                  aria-pressed={on}
                  onClick={() => pick(v.id)}
                  sx={{
                    px: 1,
                    height: 22,
                    borderRadius: "4px",
                    typography: "s3",
                    fontWeight: on ? 600 : 500,
                    color: on ? "text.primary" : "text.subtitle",
                    bgcolor: on ? "action.selected" : "transparent",
                    "&:hover": on ? undefined : { color: "text.primary" },
                  }}
                >
                  {v.label}
                </ButtonBase>
              </CustomTooltip>
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
            color: "text.subtitle",
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
        {row.expected && (
          <>
            <Typography className="sc-l">Passes when</Typography>
            <Typography className="sc-v">{row.expected}</Typography>
          </>
        )}
        {row.checks?.length > 0 && (
          <>
            <Typography className="sc-l">Graded on</Typography>
            <Typography className="sc-v">
              {row.checks.map((c, i) => (
                <Fragment key={c}>
                  {i > 0 && <Sep />}
                  {c}
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
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => {
            if (note !== (answer.note || ""))
              onAnswer({ verdict: "not-right", note });
          }}
          placeholder="What's wrong: an unrealistic caller, a check that's off, a missing step"
          inputProps={{ "aria-label": "What's wrong" }}
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
          {`Answered on ${answer.carriedFrom}${answer.by ? ` by ${answer.by}` : ""}, unchanged since`}
        </Typography>
      )}
    </Box>
  );
}
SpotCheckRow.propTypes = {
  row: PropTypes.object.isRequired,
  answer: PropTypes.object,
  onAnswer: PropTypes.func.isRequired,
};

// Vel's scenario quality review (PRD v7 §4.10.9): whether the scenarios can be
// trusted. The quality gate, the human spot-check and rule coverage. The API
// has none of it, so the drawer runs on his sample and every verdict is kept
// only on screen.
function QualityReviewBody() {
  const [answers, setAnswers] = useState(sampleAnswers);
  const gate = useMemo(() => sampleGateOf(answers), [answers]);

  const onAnswer = (id, answer) => {
    setAnswers((prev) => {
      const next = { ...prev };
      if (answer.verdict) next[id] = answer;
      else delete next[id];
      return next;
    });
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };

  return (
    <Stack sx={{ height: "100%" }}>
      <Box
        sx={{
          pl: 2.5,
          pr: 6,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            Quality review
          </Typography>
          <QualityStateChip gate={gate} />
          <UiOnlyFlag feature="scenarioStates" />
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}>
          How far the scenarios can be trusted: gated, spot-checked by a person,
          every rule tested.
        </Typography>
      </Box>

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

        <Part
          title="Quality gate"
          figure={`${gate.admitted} of ${gate.total}`}
          sub="Staged, solvable, not vacuous, alternate paths, mutation and exploit controls."
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
                    sx={{ color: "error.main", flexShrink: 0, mt: "3px" }}
                  />
                  <Box flex={1} minWidth={0}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 500 }}
                    >
                      {q.title}
                    </Typography>
                    {q.summary && (
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.subtitle" }}
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
                </Stack>
              ))}
            </Stack>
          )}
        </Part>

        <Part
          title="Human spot-check"
          figure={
            gate.agreement == null
              ? `0 of ${gate.sampled}`
              : `${pct(gate.agreement)} agree`
          }
          sub={`${gate.answered} of ${gate.sampled} checked · the bar is ${pct(gate.bar)} agreement over the whole sample${gate.carried ? ` · ${gate.carried} carried from ${gate.carriedFrom}, unchanged since` : ""}.`}
        >
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
            sx={{ mx: -2.5 }}
          >
            {SAMPLE_SPOT_CHECK.map((row) => (
              <SpotCheckRow
                key={row.id}
                row={row}
                answer={answers[row.id] || null}
                onAnswer={(a) => onAnswer(row.id, a)}
              />
            ))}
          </Stack>
        </Part>

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
                >
                  {`No scenario tests "${r}"`}
                </Typography>
              ))}
            </Stack>
          )}
        </Part>
      </Box>
    </Stack>
  );
}

export default function QualityReviewDrawer({ open, onClose }) {
  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", md: 640 }}>
      {open && <QualityReviewBody />}
    </SideDrawer>
  );
}
QualityReviewDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
