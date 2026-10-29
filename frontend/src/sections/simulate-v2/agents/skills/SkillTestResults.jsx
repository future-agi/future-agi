import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { StatusDot } from "../../components/primitives";
import { CASE_GROUPS, VERDICTS } from "../../_mock/skills";

const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 500,
  py: 0.75,
  px: 1.5,
  bgcolor: "transparent",
  whiteSpace: "nowrap",
};
const cell = { py: 0.75, px: 1.5, borderColor: "divider", typography: "s3" };
const num = { ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" };
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const GROUP_LABEL = Object.fromEntries(CASE_GROUPS.map((g) => [g.id, g.label]));

/* Colour on the change only: green better, red worse, grey flat. */
function Delta({ value, unit = " pts", lowerIsBetter = false, digits = 0 }) {
  if (value == null) return <span>—</span>;
  const flat = Math.abs(value) < 10 ** -digits / 2;
  const better = lowerIsBetter ? value < 0 : value > 0;
  return (
    <Box
      component="span"
      sx={{
        color: flat ? "text.tertiary" : better ? "success.main" : "error.main",
      }}
    >
      {flat
        ? "0"
        : `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}${unit}`}
    </Box>
  );
}
Delta.propTypes = {
  value: PropTypes.number,
  unit: PropTypes.string,
  lowerIsBetter: PropTypes.bool,
  digits: PropTypes.number,
};

/**
 * A finished skill test (skills.md "What results reveal"): the verdict,
 * then task success, skill use, cost and latency as separate rows — a gain
 * with a regression is a tradeoff, not a green score. Activation is
 * Observed only with loading events; otherwise Unknown.
 */
export default function SkillTestResults({ test, summary }) {
  const {
    results,
    base,
    cand,
    groups,
    help,
    regressions,
    contaminated,
    verdict,
    reps,
  } = summary;
  const v = VERDICTS[verdict];
  const runsOf = (g) => g.n * reps;
  const metric = (label, get, deltaOf) => ({ label, get, deltaOf });
  const rows = [
    ...groups
      .filter((g) => g.n)
      .flatMap((g) => [
        metric(
          `${GROUP_LABEL[g.id]} · passed`,
          (r) => {
            const p = r.rows
              .filter((x) => x.case.group === g.id)
              .reduce((a, x) => a + x.passes, 0);
            return `${pct(p, runsOf(g))}%  ${p}/${runsOf(g)}`;
          },
          () => (
            <Delta value={pct(g.cand, runsOf(g)) - pct(g.base, runsOf(g))} />
          ),
        ),
        metric(
          g.id === "negative"
            ? "Wrong loads"
            : g.id === "positive"
              ? "Skill loaded"
              : "Skill loaded next to another",
          (r) => {
            if (!r.cfg.skills.some((x) => x.id === test.skillId))
              return "Not loaded";
            if (!test.observed) return "Unknown";
            const n = r.rows
              .filter((x) => x.case.group === g.id)
              .reduce((a, x) => a + (x.loaded || 0), 0);
            return `${n} of ${runsOf(g)} runs`;
          },
          () => null,
        ),
      ]),
    metric(
      "Cost per run",
      (r) => `${r.cost.toFixed(1)} credits`,
      () => (
        <Delta
          value={pct(cand.cost - base.cost, base.cost)}
          unit="%"
          lowerIsBetter
        />
      ),
    ),
    metric(
      "Latency, median",
      (r) => `${r.latency.toFixed(1)}s`,
      () => (
        <Delta
          value={cand.latency - base.latency}
          unit="s"
          lowerIsBetter
          digits={1}
        />
      ),
    ),
  ];

  return (
    <Stack spacing={2}>
      <Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <StatusDot status={v.tone} size={8} live={false} />
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {v.label}
          </Typography>
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          {verdict === "rejected"
            ? `The skill's files contain ${contaminated.length} of the tested cases and what to answer, so passing them proves nothing. Remove those lines and test again.`
            : verdict === "tradeoff"
              ? `Where it should help, passed ${help.cand}/${runsOf(help)} against ${help.base}/${runsOf(help)}. But ${regressions.length} case${regressions.length === 1 ? "" : "s"} it shouldn't touch got worse, so it isn't an unqualified win.`
              : verdict === "helps"
                ? `Passed ${help.cand}/${runsOf(help)} where it should help, against ${help.base}/${runsOf(help)}, and nothing it shouldn't touch got worse.`
                : "The candidate didn't pass more of the cases it should help than the baseline did."}
          {!test.observed &&
            " Activation is unknown on this runtime, so the outcomes don't say whether the skill was used."}
        </Typography>
        {contaminated.length > 0 && (
          <Box
            sx={{
              mt: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            {contaminated.map((c) => (
              <Typography
                key={c.title}
                sx={{
                  typography: "s3",
                  px: 1.5,
                  py: 0.75,
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                {`“${c.title}” is written into ${c.file}`}
              </Typography>
            ))}
          </Box>
        )}
      </Box>

      {/* Each measure on its own row; colour only on the change. */}
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell sx={head} />
            {results.map((r) => (
              <TableCell key={r.cfg.id} sx={{ ...head, textAlign: "right" }}>
                {r.cfg.label}
              </TableCell>
            ))}
            <TableCell
              sx={{ ...head, textAlign: "right" }}
            >{`Change vs ${base.cfg.id === "none" ? "no skill" : "current"}`}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((m) => (
            <TableRow key={m.label}>
              <TableCell sx={{ ...cell, color: "text.secondary" }}>
                {m.label}
              </TableCell>
              {results.map((r) => (
                <TableCell key={r.cfg.id} sx={num}>
                  {m.get(r)}
                </TableCell>
              ))}
              <TableCell sx={num}>{m.deltaOf()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {/* Case by case. */}
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          {`By case · ${test.cases.length}`}
        </Typography>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={head}>Case</TableCell>
              {results.map((r) => (
                <TableCell key={r.cfg.id} sx={{ ...head, textAlign: "right" }}>
                  {r.cfg.candidate
                    ? "Candidate"
                    : r.cfg.id === "none"
                      ? "No skill"
                      : "Current"}
                </TableCell>
              ))}
              <TableCell sx={head}>Skill use · candidate</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {test.cases.map((c) => {
              const cr = cand.rows.find(
                (x) => x.case.scenarioId === c.scenarioId,
              );
              const br = base.rows.find(
                (x) => x.case.scenarioId === c.scenarioId,
              );
              const worse = cr.passes < br.passes && c.group !== "positive";
              return (
                <TableRow key={c.scenarioId}>
                  <TableCell sx={{ ...cell, maxWidth: 0, width: "100%" }}>
                    <Typography
                      noWrap
                      title={c.title}
                      sx={{
                        typography: "s3",
                        fontWeight: 500,
                        color: "text.primary",
                      }}
                    >
                      {c.title}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: worse ? "error.main" : "text.tertiary",
                      }}
                    >
                      {[GROUP_LABEL[c.group], cr.note]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </TableCell>
                  {results.map((r) => {
                    const x = r.rows.find(
                      (y) => y.case.scenarioId === c.scenarioId,
                    );
                    return (
                      <TableCell key={r.cfg.id} sx={num}>
                        {`${x.passes}/${reps}`}
                      </TableCell>
                    );
                  })}
                  <TableCell
                    sx={{
                      ...cell,
                      whiteSpace: "nowrap",
                      color: "text.secondary",
                    }}
                  >
                    {!test.observed
                      ? "Unknown"
                      : cr.loaded
                        ? `${c.group === "negative" ? "Loaded, shouldn't" : "Loaded"} ${cr.loaded}/${reps}`
                        : "Not loaded"}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </Stack>
  );
}

SkillTestResults.propTypes = {
  test: PropTypes.object.isRequired,
  summary: PropTypes.object.isRequired,
};
