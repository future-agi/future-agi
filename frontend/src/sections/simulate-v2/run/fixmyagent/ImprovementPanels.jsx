import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
import { contractMessage } from "../../_mock/improvementPolicy";
import { KIND_SURFACE, staticFailed } from "../../_mock/optimizer";
import { changeFileFor } from "../../_mock/optimize";
import { scenarioTitle } from "../../_mock/scenarios";

/*
  The updated RSI PRD's parts of an improvement page, each small: the proof
  label, the finalists scored once on the held-back set, the must-pass set,
  a contract-changing candidate, the scenarios the improvement wrote, and
  which members and channels a change touches.
*/

const heading = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.5,
  mb: 0.75,
};
const box = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

/**
 * The finalists — three by default — and their held-back scores. Only they
 * were scored there, once, at the end; the winner is the best that passes.
 */
export function FinalistsTable({ result, bare = false }) {
  const list = result?.finalists || [];
  if (!list.length) return null;
  return (
    <Box sx={bare ? undefined : { px: 2.5, pt: 2.25 }}>
      {!bare && (
        <Typography
          sx={heading}
        >{`Finalists · scored once on the held-back set${result.textSearch ? " · as real calls" : ""}`}</Typography>
      )}
      {/* Inside a tab: one quiet line says what the table is. */}
      {bare && (
        <Typography
          sx={{
            px: 2,
            py: 1,
            typography: "s3",
            color: "text.tertiary",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {`Scored once on held-back scenarios, never shown while searching${result.textSearch ? ", as real calls" : ""} — the winner is the best that passes.`}
        </Typography>
      )}
      <Box sx={bare ? undefined : box}>
        <Stack
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {["Run", "Target", "Held back", ""].map((h, i) => (
            <Typography
              key={h || i}
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontWeight: 600,
                width: i < 3 ? 90 : "auto",
                flex: i === 3 ? 1 : "none",
              }}
            >
              {h}
            </Typography>
          ))}
        </Stack>
        {list.map((f, i) => {
          const lift = f.heldScore - result.heldBase;
          return (
            <Stack
              key={f.n}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 1.5,
                py: 0.875,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: f.winner ? 600 : 500,
                  width: 90,
                  fontVariantNumeric: "tabular-nums",
                }}
              >{`Run ${f.n}`}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  width: 90,
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >{`${f.score}%`}</Typography>
              <Typography
                sx={{
                  typography: "s2",
                  width: 90,
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${f.heldScore}%`}
                <Box
                  component="span"
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontWeight: 400,
                    ml: 0.75,
                  }}
                >{`${lift >= 0 ? "+" : "−"}${Math.abs(lift)}`}</Box>
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                sx={{ flex: 1, minWidth: 0 }}
              >
                <Iconify
                  icon={
                    f.accepted
                      ? "solar:check-circle-linear"
                      : "solar:close-circle-linear"
                  }
                  width={14}
                  sx={{
                    color: f.accepted ? "text.secondary" : "text.disabled",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {f.winner
                    ? "Winner — the best finalist that passes"
                    : f.accepted
                      ? "Passes"
                      : f.why}
                </Typography>
              </Stack>
            </Stack>
          );
        })}
      </Box>
    </Box>
  );
}
FinalistsTable.propTypes = { result: PropTypes.object, bare: PropTypes.bool };

/**
 * The must-pass set in one line, and the flaky scenarios beside it — shown on
 * the proposal, never disqualifying.
 */
export function MustPassNote({ result, bare = false }) {
  const [open, setOpen] = useState(false);
  const m = result?.mustPass;
  if (!m) return null;
  const flaky = result.flakyToday || [];
  const once = (result.winner?.mustPassFails || []).filter((f) => !f.repeated);
  const parts = [
    m.marked ? `${m.marked} you marked` : null,
    m.remembered ? `${m.remembered} from regression memory` : null,
    `${m.passing} that pass every repeat today`,
  ].filter(Boolean);
  return (
    <Box sx={bare ? null : { px: 2.5, pt: 1.25 }}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`Must-pass set: ${m.total} — ${parts.join(", ")}. A failure disqualifies only when it repeats on a re-run.`}
        {once.length
          ? ` ${once.length} failed once on the winner and passed the re-run.`
          : ""}
        {flaky.length ? " " : ""}
        {flaky.length > 0 && (
          <Box
            component="span"
            {...clickable(() => setOpen((o) => !o), { expanded: open })}
            sx={{
              color: "text.secondary",
              fontWeight: 600,
              cursor: "pointer",
              "&:hover": { color: "text.primary" },
              ...focusRing,
            }}
          >
            {`${flaky.length} flaky today — shown, never disqualifying`}
          </Box>
        )}
      </Typography>
      {open && (
        <Stack sx={{ ...box, mt: 0.75 }}>
          {flaky.map((t, i) => (
            <Typography
              key={t.id}
              noWrap
              sx={{
                typography: "s3",
                color: "text.secondary",
                px: 1.5,
                py: 0.625,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              {scenarioTitle(t.title)}
            </Typography>
          ))}
        </Stack>
      )}
    </Box>
  );
}
MustPassNote.propTypes = { result: PropTypes.object, bare: PropTypes.bool };

/**
 * A candidate that adds a tool or a tool parameter can't be proven on the
 * frozen environment version. Approving a new version with the new contract
 * runs the baseline again on it before anything is compared.
 */
export function ContractNotice({
  contract,
  envVersion,
  nextVersion,
  approval,
  onApprove,
}) {
  if (!contract) return null;
  const next =
    nextVersion ||
    (envVersion && /^v\d+$/.test(envVersion)
      ? `v${Number(envVersion.slice(1)) + 1}`
      : "a new version");
  return (
    <Box sx={{ px: 2.5, pt: 2.25 }}>
      <Typography sx={heading}>Changes the contract</Typography>
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="flex-start"
        sx={{ ...box, px: 1.5, py: 1.25 }}
      >
        <Iconify
          icon="solar:document-add-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {contract.title}
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {approval
              ? approval.candidate
                ? `Approved environment ${approval.to} with the new contract — ${contract.tool} takes ${contract.param}. The baseline ran again on ${approval.to}: change ${approval.candidate.train}% vs baseline ${approval.baseline.train}% on targets (${signedPts(approval.candidate.train - approval.baseline.train)}).`
                : `Approved environment ${approval.to} with the new contract — ${contract.tool} takes ${contract.param}. Baseline on ${approval.to}: ${approval.baseline.train}% on targets.`
              : contractMessage(contract, envVersion || "the frozen version")}
          </Typography>
          {contract.why && !approval && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
            >
              {contract.why}
            </Typography>
          )}
        </Box>
        {!approval && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => onApprove?.(next)}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            {`Approve ${next} and re-run the baseline`}
          </Button>
        )}
      </Stack>
    </Box>
  );
}
ContractNotice.propTypes = {
  contract: PropTypes.object,
  envVersion: PropTypes.string,
  nextVersion: PropTypes.string,
  approval: PropTypes.object,
  onApprove: PropTypes.func,
};

const signedPts = (n) => `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n)} pts`;

/**
 * Each finalist ran once in every other environment picked; each must-pass
 * set there had to hold. Targets stay the main environment's.
 */
export function OtherEnvironments({ result }) {
  const finalists = (result?.finalists || []).filter(
    (f) => (f.envs || []).length,
  );
  if (!finalists.length) return null;
  const envs = finalists[0].envs.map((e) => ({
    id: e.envId,
    name: e.name,
    mustPass: e.mustPass,
  }));
  return (
    <Box sx={{ px: 2.5, pt: 2.25 }}>
      <Typography
        sx={heading}
      >{`Other environments · each finalist ran once in ${envs.length === 1 ? "it" : `all ${envs.length}`}`}</Typography>
      <Box sx={box}>
        <Stack
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontWeight: 600,
              flex: 1,
            }}
          >
            Environment
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontWeight: 600,
              width: 90,
            }}
          >
            Must-pass
          </Typography>
          {finalists.map((f) => (
            <Typography
              key={f.n}
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontWeight: f.winner ? 600 : 500,
                width: 150,
              }}
            >{`Run ${f.n}${f.winner ? " · winner" : ""}`}</Typography>
          ))}
        </Stack>
        {envs.map((e, i) => (
          <Stack
            key={e.id}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 1.5,
              py: 0.875,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
              {e.name}
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                width: 90,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {e.mustPass}
            </Typography>
            {finalists.map((f) => {
              const run = f.envs.find((x) => x.envId === e.id);
              const bad = run && !run.held;
              return (
                <Stack
                  key={f.n}
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ width: 150, minWidth: 0 }}
                >
                  <Iconify
                    icon={
                      bad
                        ? "solar:close-circle-bold"
                        : "solar:check-circle-linear"
                    }
                    width={14}
                    sx={{
                      color: bad ? "status.danger.fg" : "text.secondary",
                      flexShrink: 0,
                    }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      color: bad ? "status.danger.fg" : "text.tertiary",
                    }}
                  >
                    {!run
                      ? "—"
                      : bad
                        ? `“${run.broke.title}” failed twice`
                        : run.broke
                          ? "held · 1 failed once"
                          : "held"}
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        ))}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        Targets come from the main environment only — here a finalist just has
        to keep what passes today.
      </Typography>
    </Box>
  );
}
OtherEnvironments.propTypes = { result: PropTypes.object };

/**
 * Candidates stopped at Static checks — they broke a rule before anything
 * ran (an area switched off, a protected part, a setting out of range, the
 * judges), so none of them was scored or could win.
 */
export function StaticNotice({ result }) {
  const stopped = (result?.trials || []).filter(staticFailed);
  if (!stopped.length) return null;
  return (
    <Box sx={{ px: 2.5, pt: 2.25 }}>
      <Typography
        sx={heading}
      >{`Stopped at Static checks · ${stopped.length} of ${result.trials.length}`}</Typography>
      <Box sx={box}>
        {stopped.map((t, i) => (
          <Stack
            key={t.n}
            direction="row"
            alignItems="flex-start"
            spacing={1.25}
            sx={{
              px: 1.5,
              py: 0.875,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:shield-cross-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
            />
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                width: 64,
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >{`Run ${t.n}`}</Typography>
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {t.static.reason}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Box>
  );
}
StaticNotice.propTypes = { result: PropTypes.object };

const CODE_SURFACES = ["code", "structure"];

/**
 * The plan a code or structural change was built to — root cause, the steps,
 * the files — shown above its diff, and where it was built.
 */
export function CodePlan({ env, record, proposals = [] }) {
  const code = proposals.filter((p) =>
    CODE_SURFACES.includes(KIND_SURFACE[p.kind] || "code"),
  );
  if (!code.length) return null;
  const cause =
    record?.snapshot?.rootCause ||
    record?.diagnosis?.[0]?.headline ||
    code[0].why;
  const tests = record?.result?.repoTests || record?.frozen?.repoTests || null;
  const files = [
    ...new Set([
      ...code.map((p) => changeFileFor(env, p)?.path).filter(Boolean),
      ...(tests
        ? code.map(
            (p) =>
              `${env?.id || "agent"}/tests/test_${String(p.step || p.id)
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, "_")}.py`,
          )
        : []),
    ]),
  ];
  const steps = [
    ...code.map((p) => p.title),
    ...(tests
      ? [`Add a test for each, then run ${tests} and the full regression set`]
      : ["Run the full regression set"]),
  ];
  const mono = "ui-monospace, Menlo, monospace";
  return (
    <Box sx={{ px: 2, py: 1.5 }}>
      <Typography sx={{ ...heading, mb: 0.5 }}>Plan</Typography>
      <Typography
        sx={{ typography: "s2", color: "text.secondary" }}
      >{`Root cause: ${String(cause || "—").replace(/\.$/, "")}.`}</Typography>
      <Stack component="ol" spacing={0.25} sx={{ m: 0, mt: 0.75, pl: 2.5 }}>
        {steps.map((st) => (
          <Typography key={st} component="li" sx={{ typography: "s2" }}>
            {st}
          </Typography>
        ))}
      </Stack>
      <Stack
        direction="row"
        flexWrap="wrap"
        columnGap={1.5}
        rowGap={0.25}
        sx={{ mt: 0.75 }}
      >
        {files.map((f) => (
          <Typography
            key={f}
            sx={{ typography: "s3", fontFamily: mono, color: "text.secondary" }}
          >
            {f}
          </Typography>
        ))}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ mt: 0.75 }}
      >
        <Iconify
          icon="solar:branching-paths-up-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`Built in its own sandboxed worktree · branch fagi/${String(record?.id || "improvement").toLowerCase()}`}
        </Typography>
      </Stack>
    </Box>
  );
}
CodePlan.propTypes = {
  env: PropTypes.object,
  record: PropTypes.object,
  proposals: PropTypes.array,
};

/**
 * Code candidates that didn't fit the sealed environment — they moved the
 * entry point, changed the build or changed how the agent is started, so
 * they couldn't be tested in it and stopped there. Never eligible to win;
 * rebuilding the environment from the candidate's commit is how to test one.
 */
export function UnfitNotice({ result }) {
  const unfit = (result?.trials || []).filter(
    (t) => t.compat && t.compat.pass === false,
  );
  if (!unfit.length) return null;
  return (
    <Box sx={{ px: 2.5, pt: 2.25 }}>
      <Typography
        sx={heading}
      >{`Didn't fit the sealed environment · ${unfit.length} of ${result.trials.length}`}</Typography>
      <Box sx={box}>
        {unfit.map((t, i) => (
          <Stack
            key={t.n}
            direction="row"
            alignItems="flex-start"
            spacing={1.25}
            sx={{
              px: 1.5,
              py: 0.875,
              borderTop: i ? "1px solid" : "none",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:box-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
            />
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                width: 64,
                flexShrink: 0,
                fontVariantNumeric: "tabular-nums",
              }}
            >{`Run ${t.n}`}</Typography>
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {t.compat.reason}
            </Typography>
          </Stack>
        ))}
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
        Checked before any scenario ran, so none were scored.
      </Typography>
    </Box>
  );
}
UnfitNotice.propTypes = { result: PropTypes.object };

/**
 * Scenarios the improvement wrote stay with it until someone adds them —
 * "Add N scenarios to the environment".
 */
export function IssueScenariosNotice({ rows, envName, onAdd }) {
  if (!rows?.length) return null;
  const fresh = rows.filter((r) => r.freshVariation).length;
  const fromTraces = rows.length - fresh;
  return (
    <Box sx={{ px: 2.5, pt: 2.25 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ ...box, px: 1.5, py: 1 }}
      >
        <Iconify
          icon="solar:list-check-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${rows.length} ${rows.length === 1 ? "scenario" : "scenarios"} from this improvement`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`${[fromTraces ? `${fromTraces} from the issue's traces` : null, fresh ? `${fresh} fresh ${fresh === 1 ? "variation" : "variations"} from the held-back check` : null].filter(Boolean).join(", ")}. They stay with the improvement until you add them${envName ? ` to ${envName}` : ""} — as proposed scenarios.`}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={onAdd}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {`Add ${rows.length} ${rows.length === 1 ? "scenario" : "scenarios"} to the environment`}
        </Button>
      </Stack>
    </Box>
  );
}
IssueScenariosNotice.propTypes = {
  rows: PropTypes.array,
  envName: PropTypes.string,
  onAdd: PropTypes.func,
};

/**
 * Which members a proposal changes, and the channels a shared-part change
 * wasn't checked on.
 */
export function ChangeScope({ scope, channelNotes = [], target }) {
  const lines = [
    scope?.hasMembers
      ? scope.names.length
        ? `Changes ${scope.names.join(", ")}${scope.shared ? ", and parts every member shares" : ""}`
        : "Changes parts every member shares"
      : null,
    target ? `Target member: ${target}` : null,
    ...(scope?.shared || !scope?.hasMembers ? channelNotes : []),
  ].filter(Boolean);
  if (!lines.length) return null;
  return (
    <Stack direction="row" flexWrap="wrap" columnGap={1.5} rowGap={0.25}>
      {lines.map((l) => (
        <Typography key={l} sx={{ typography: "s3", color: "text.secondary" }}>
          {l}
        </Typography>
      ))}
    </Stack>
  );
}
ChangeScope.propTypes = {
  scope: PropTypes.object,
  channelNotes: PropTypes.array,
  target: PropTypes.string,
};
