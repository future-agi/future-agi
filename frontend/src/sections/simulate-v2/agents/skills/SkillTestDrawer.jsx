import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import CopyCommandButton from "../../components/CopyCommandButton";
import { neutralCheckboxSx } from "../../components/primitives";
import { estimateRunPlan, rangeLabel } from "../../_mock/pricing";
import {
  CASE_GROUPS,
  latestOf,
  skillTestCli,
  suggestCasesOf,
  testConfigsOf,
  testStatusOf,
  testSummaryOf,
} from "../../_mock/skills";
import { MONO } from "../agentViewUtils";
import { segmentedToggleSx } from "../segmentedToggleSx";
import { FactBox, FactRow } from "./SkillFacts";
import SkillTestResults from "./SkillTestResults";

const skillMdOf = (skill) =>
  latestOf(skill).files.find((f) => f.path === "SKILL.md")?.text || "";

const REPEATS = [1, 3, 5];
const label = { typography: "s3", color: "text.tertiary", mb: 0.5 };
const rowSx = {
  px: 1.25,
  minHeight: 36,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

/**
 * Test a skill (skills.md "Choose tests", "Run and compare"): the agent
 * without it, its current version and the candidate with the skill, on the
 * same fixed cases — ones where it should be used, ones where it shouldn't,
 * and ones another skill also fits. Same evaluations on every
 * configuration; nothing about the expected answers goes into the skill.
 * With `test`, shows a started test read-only.
 */
export default function SkillTestDrawer({
  open,
  onClose,
  agent,
  skill,
  set,
  runtime,
  envs,
  test,
  onStart,
  prefill,
  onDecide,
}) {
  const [envId, setEnvId] = useState(null);
  /* Keep needs a second click when the candidate came with a regression. */
  const [confirmKeep, setConfirmKeep] = useState(false);
  const [revising, setRevising] = useState(null);
  const [cases, setCases] = useState([]);
  const [repeats, setRepeats] = useState(3);
  const [, tick] = useState(0);

  const env = envs.find((e) => e.env.id === (test?.envId || envId)) || envs[0];
  const configs = useMemo(
    () => (test ? test.configs : skill ? testConfigsOf(skill, set, agent) : []),
    [test, skill, set, agent],
  );

  useEffect(() => {
    setConfirmKeep(false);
    setRevising(null);
    if (!open || test || !skill) return;
    setEnvId(prefill?.envId || envs[0]?.env.id || null);
    setRepeats(prefill?.repeats || 3);
  }, [open, test, skill, envs, prefill]);
  useEffect(() => {
    if (!open || test || !skill || !env) return;
    /* A revision is tested on exactly the cases the last version was. */
    if (prefill) {
      setCases(prefill.cases.map((c) => ({ ...c, picked: true })));
      return;
    }
    const others = set.skills.filter(
      (s) => s.id !== skill.id && s.state === "on",
    );
    setCases(suggestCasesOf(skill, others, env.envState?.scenarios || []));
  }, [open, test, skill, env, set, prefill]);
  /* A running test finishes on its own; re-read its status. */
  useEffect(() => {
    if (!open || !test || testStatusOf(test) !== "running") return undefined;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [open, test]);

  if (!open || !skill) return <SideDrawer open={false} onClose={onClose} />;

  const shown = test ? test.cases : cases;
  const picked = shown.filter((c) => c.picked);
  const reps = test ? test.repeats : repeats;
  const estimate = env
    ? estimateRunPlan({
        env: env.env,
        envState: env.envState,
        scenarios: picked.length,
        attempts: reps,
        versions: configs.map((c) => c.id),
      })
    : null;
  const total = picked.length * reps * configs.length;
  /* Shared cases count: the skill should be used there too. */
  const positives = picked.filter((c) => c.group !== "negative").length;
  const negatives = picked.filter((c) => c.group === "negative").length;
  const status = test ? testStatusOf(test) : null;
  const locked = !!test || !!prefill;
  const summary =
    test && status === "done"
      ? testSummaryOf(test, skill, env?.envState?.scenarios || [])
      : null;
  const decided = test?.decision;
  const canKeep = summary && summary.verdict !== "rejected";
  /* The editor belongs to a finished test; a new plan starts without it. */
  const editing = !!test && revising != null;
  const edit = (id, p) =>
    setCases((cs) => cs.map((c) => (c.scenarioId === id ? { ...c, ...p } : c)));
  const blockers = [
    !env &&
      "This agent isn't in an environment yet. Build one to test against.",
    env &&
      !positives &&
      "Pick at least one case where the skill should be used.",
    env &&
      !negatives &&
      "Pick at least one case where it shouldn't, or wrong activations go unseen.",
  ].filter(Boolean);

  return (
    <SideDrawer open onClose={onClose} width={960}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              {test ? `${test.id} · Test of ${skill.id}` : `Test ${skill.id}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {test
                ? `${status === "running" ? "Running" : status === "cancelled" ? "Cancelled" : "Finished"} · started by ${test.by} · ${new Date(test.startedAt).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
                : `Against the agent without it, on the same cases · ${agent.name}`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          <Stack spacing={2.5}>
            {summary && <SkillTestResults test={test} summary={summary} />}
            {decided && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {decided.line}
              </Typography>
            )}
            {editing && (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  p: 1.5,
                }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {`Revise ${skill.id} as v${latestOf(skill).v + 1}`}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}
                >
                  {`Change when it's used (the description) or its instructions. It's tested next on the same ${test.cases.length} cases and evaluations as ${test.id}, so the results compare.`}
                </Typography>
                <TextField
                  multiline
                  fullWidth
                  minRows={6}
                  value={revising}
                  onChange={(e) => setRevising(e.target.value)}
                  inputProps={{ style: { fontFamily: MONO, fontSize: 12 } }}
                />
                <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={revising === skillMdOf(skill)}
                    onClick={() => onDecide("revise", { test, text: revising })}
                  >
                    {`Save v${latestOf(skill).v + 1} and test it`}
                  </Button>
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() => setRevising(null)}
                  >
                    Cancel
                  </Button>
                </Stack>
              </Box>
            )}
            {summary && (
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  pt: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                What ran
              </Typography>
            )}
            {prefill && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`Same cases, repeats and evaluations as ${prefill.id}. They stay fixed so the versions compare.`}
              </Typography>
            )}
            {/* What runs side by side. */}
            <Box>
              <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
                {`What's compared · ${configs.length} configurations`}
              </Typography>
              <FactBox>
                {configs.map((c) => (
                  <FactRow
                    key={c.id}
                    k={
                      c.asIs
                        ? "Current"
                        : c.candidate
                          ? "Candidate"
                          : c.id === "none"
                            ? "No skill"
                            : "Current"
                    }
                  >
                    {c.label}
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary" }}
                    >{` · ${c.detail}`}</Box>
                  </FactRow>
                ))}
                <FactRow k="Activation">
                  {runtime.observed
                    ? "Observed from skill-loading events"
                    : "Unknown on this runtime"}
                  <Box component="span" sx={{ color: "text.tertiary" }}>
                    {runtime.observed
                      ? " · reported only where a loading event is seen"
                      : " · no loading events, so results show task outcomes without claiming the skill was used"}
                  </Box>
                </FactRow>
              </FactBox>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
              >
                {`Only the candidate changes. ${agent.production || agent.latest} keeps its skills, and the environment's evaluations score every configuration the same way.`}
              </Typography>
            </Box>

            {/* Where. */}
            <Stack direction="row" spacing={2} alignItems="flex-end">
              <Box sx={{ width: 320 }}>
                <Typography sx={label}>Environment</Typography>
                {envs.length ? (
                  <Select
                    size="small"
                    fullWidth
                    disabled={locked}
                    value={env?.env.id || ""}
                    onChange={(e) => setEnvId(e.target.value)}
                  >
                    {envs.map((e) => (
                      <MenuItem key={e.env.id} value={e.env.id}>
                        {`${e.env.name} · ${(e.envState?.scenarios || []).filter((s) => !s.archived && !s.retired && !s.proposed).length} scenarios`}
                      </MenuItem>
                    ))}
                  </Select>
                ) : (
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    None yet
                  </Typography>
                )}
              </Box>
              <Box>
                <Typography sx={label}>Repeats per case</Typography>
                <ToggleButtonGroup
                  exclusive
                  size="small"
                  value={reps}
                  disabled={locked}
                  onChange={(_, v) => v && setRepeats(v)}
                  sx={segmentedToggleSx}
                >
                  {REPEATS.map((r) => (
                    <ToggleButton key={r} value={r}>
                      {`${r}×`}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Box>
            </Stack>

            {/* The cases, by what each one tests. */}
            {env &&
              CASE_GROUPS.map((g) => {
                const rows = shown.filter((c) => c.group === g.id);
                if (test && !rows.some((c) => c.picked)) return null;
                return (
                  <Box key={g.id}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {`${g.label} · ${rows.filter((c) => c.picked).length}`}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mb: 0.75,
                      }}
                    >
                      {g.line}
                    </Typography>
                    {rows.length ? (
                      <Box
                        sx={{
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: "8px",
                        }}
                      >
                        {rows
                          .filter((c) => !test || c.picked)
                          .map((c) => (
                            <Stack
                              key={c.scenarioId}
                              direction="row"
                              alignItems="center"
                              spacing={1}
                              sx={rowSx}
                            >
                              {!locked && (
                                <Checkbox
                                  size="small"
                                  checked={!!c.picked}
                                  onChange={(e) =>
                                    edit(c.scenarioId, {
                                      picked: e.target.checked,
                                    })
                                  }
                                  sx={{ ...neutralCheckboxSx, p: 0.5 }}
                                />
                              )}
                              <Box flex={1} minWidth={0} sx={{ py: 0.5 }}>
                                <Typography
                                  noWrap
                                  sx={{ typography: "s2", fontWeight: 500 }}
                                >
                                  {c.title}
                                </Typography>
                                <Typography
                                  noWrap
                                  sx={{
                                    typography: "s3",
                                    color: "text.tertiary",
                                  }}
                                >
                                  {[c.detail, c.why]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </Typography>
                              </Box>
                              {!locked && (
                                <Select
                                  size="small"
                                  value={c.group}
                                  onChange={(e) =>
                                    edit(c.scenarioId, {
                                      group: e.target.value,
                                      picked: true,
                                      why: "Moved here by you",
                                    })
                                  }
                                  sx={{
                                    width: 180,
                                    fontSize: 12,
                                    "& .MuiSelect-select": { py: 0.5 },
                                  }}
                                >
                                  {CASE_GROUPS.map((x) => (
                                    <MenuItem
                                      key={x.id}
                                      value={x.id}
                                      sx={{ fontSize: 12 }}
                                    >
                                      {x.label}
                                    </MenuItem>
                                  ))}
                                </Select>
                              )}
                            </Stack>
                          ))}
                      </Box>
                    ) : (
                      <Typography
                        sx={{ typography: "s2", color: "text.secondary" }}
                      >
                        {g.id === "positive"
                          ? "No scenario in this environment mentions what the skill is for. Move one here, or add one to the environment."
                          : g.id === "interaction"
                            ? "No scenario also fits another loaded skill."
                            : "Every scenario mentions the skill."}
                      </Typography>
                    )}
                  </Box>
                );
              })}

            {env && (
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  From code:
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                    wordBreak: "break-all",
                  }}
                >
                  {skillTestCli({
                    skill,
                    agent,
                    env: env.env,
                    cases: shown,
                    repeats: reps,
                  })}
                </Typography>
                <CopyCommandButton
                  command={skillTestCli({
                    skill,
                    agent,
                    env: env.env,
                    cases: shown,
                    repeats: reps,
                  })}
                />
              </Stack>
            )}
          </Stack>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {`${picked.length} case${picked.length === 1 ? "" : "s"} × ${reps} × ${configs.length} configurations = ${total} runs${estimate ? ` · ${rangeLabel(estimate.platform?.low, estimate.platform?.high, estimate.platform?.unknown)}` : ""}`}
            </Typography>
            {!test && blockers[0] && (
              <Typography sx={{ typography: "s3", color: "warning.dark" }}>
                {blockers[0]}
              </Typography>
            )}
            {confirmKeep && (
              <Typography sx={{ typography: "s3", color: "warning.dark" }}>
                {`The regression on ${summary.regressions.map((r) => `“${r.case.title}”`).join(", ")} stays recorded with the version.`}
              </Typography>
            )}
            {test && status === "running" && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Running. Results appear here when every configuration has
                finished.
              </Typography>
            )}
          </Box>
          {summary && !decided && !editing && (
            <>
              {summary.regressions.length > 0 ||
              summary.verdict === "no_gain" ? (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => onDecide("improve", { test })}
                >
                  Improve with a budget
                </Button>
              ) : null}
              <Button
                size="small"
                color="inherit"
                onClick={() => onDecide("remove", { test })}
              >
                {skill.state === "on"
                  ? "Keep the current version"
                  : "Don't add it"}
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={() => setRevising(skillMdOf(skill))}
              >
                Revise
              </Button>
              {canKeep && (
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  onClick={() => {
                    if (summary.verdict === "tradeoff" && !confirmKeep) {
                      setConfirmKeep(true);
                      return;
                    }
                    onDecide("keep", { test, summary });
                  }}
                >
                  {confirmKeep
                    ? "Keep it with the regression"
                    : `Keep ${skill.id} v${test.v}`}
                </Button>
              )}
            </>
          )}
          {(!summary || decided || editing) && (
            <Button size="small" color="inherit" onClick={onClose}>
              {test ? "Close" : "Cancel"}
            </Button>
          )}
          {!test && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={blockers.length > 0}
              onClick={() =>
                onStart({
                  skillId: skill.id,
                  v: configs
                    .find((c) => c.candidate)
                    ?.skills.find((s) => s.id === skill.id)?.v,
                  envId: env.env.id,
                  envName: env.env.name,
                  configs,
                  cases: picked,
                  repeats,
                  runs: total,
                  estimate: estimate?.platform || null,
                  observed: !!runtime.observed,
                })
              }
            >
              {`Start ${total} runs`}
            </Button>
          )}
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

SkillTestDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  agent: PropTypes.object.isRequired,
  skill: PropTypes.object,
  set: PropTypes.object.isRequired,
  runtime: PropTypes.object.isRequired,
  envs: PropTypes.array.isRequired,
  test: PropTypes.object,
  onStart: PropTypes.func,
  prefill: PropTypes.object,
  onDecide: PropTypes.func,
};
