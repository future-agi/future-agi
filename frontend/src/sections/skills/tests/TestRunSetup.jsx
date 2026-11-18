import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormLabel,
  MenuItem,
  Pagination,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { paths } from "src/routes/paths";
import { isDefiniteRejection, operationStatus } from "src/api/skills/operation-errors";
import { skillFilesDigest } from 'src/api/skills/digest';
import { getSkillErrorCode, useSkillsList } from "src/api/skills";
import {
  useCreateSkillTestRun,
  usePreviewSkillTestRun,
  useSkillTestingOptions,
  useSkillV11Scope,
} from "src/api/skills/testing";
import { useSkillsSpendPolicy } from "src/api/skills/r3-spend";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import TestInventoryReview, { centsUp, providerName, sendSentence } from "./TestInventoryReview";
import CapabilityNotice from "../components/CapabilityNotice";
import ActionReason, { reasonProps } from "../components/ActionReason";
import Money from "../components/Money";
import TechnicalDetails from "../components/TechnicalDetails";
import { useSkillsAccess } from "../utils/access";
import AdminAllowanceRequest, { allowanceRequestText } from "./AdminAllowanceRequest";

// "Set allowance" carries the way back to this test: the allowance page reads ?returnTo= and offers
// "Back to tests" after saving (SH7). Read from the window so this works with or without a router.
const allowanceHref = () =>
  `${paths.dashboard.skills.allowance}?returnTo=${encodeURIComponent(`${window.location.pathname}${window.location.search}`)}`;

const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

async function draftCandidate(draft) {
  return { kind: 'draft', revision: draft.revision, digest: await skillFilesDigest(draft.files) };
}

function CompetitorPicker({ skillId, value, onChange, limit, disabled }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const query = useSkillsList({
    search,
    label: "production",
    page,
    pageSize: 25,
  });
  const choices = (query.data?.results ?? []).filter(
    (skill) =>
      skill.id !== skillId &&
      skill.labels.some((label) => label.name === "production"),
  );
  return (
    <Section
      title="Compare with other skills"
      description="Adds the chosen skills' production versions to the comparison. No other workspace skills are included."
    >
      <Stack spacing={1}>
        <TextField
          size="small"
          label="Find a competing skill"
          value={search}
          disabled={disabled}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
        />
        {query.isError ? (
          <ErrorState
            title="Could not load competing skills"
            onRetry={query.refetch}
          />
        ) : (
          <TextField
            select
            size="small"
            label="Add published competitor"
            value=""
            disabled={disabled || value.length >= limit || query.isLoading}
            onChange={(event) => {
              const skill = choices.find(
                (item) => item.id === event.target.value,
              );
              if (
                skill &&
                skill.id !== skillId &&
                !value.some((item) => item.skillId === skill.id)
              )
                onChange([
                  ...value,
                  {
                    skillId: skill.id,
                    version: skill.labels.find(
                      (label) => label.name === "production",
                    ).version,
                    name: skill.name,
                  },
                ]);
            }}
          >
            {choices.map((skill) => (
              <MenuItem
                key={skill.id}
                value={skill.id}
                disabled={value.some((item) => item.skillId === skill.id)}
              >
                {skill.name} · v
                {
                  skill.labels.find((label) => label.name === "production")
                    .version
                }
              </MenuItem>
            ))}
            {!choices.length && (
              <MenuItem disabled value="none">
                No published competitors on this page
              </MenuItem>
            )}
          </TextField>
        )}
        {query.data?.totalPages > 1 && (
          <Pagination
            size="small"
            page={page}
            count={query.data.totalPages}
            onChange={(_, next) => setPage(next)}
            disabled={disabled}
          />
        )}
        {value.map((item) => (
          <Stack
            key={item.skillId}
            direction="row"
            spacing={1}
            alignItems="center"
          >
            <Typography variant="s1" sx={{ overflowWrap: "anywhere", flex: 1 }}>
              {item.name} · v{item.version}
            </Typography>
            <Button
              disabled={disabled}
              size="small"
              onClick={() =>
                onChange(
                  value.filter((other) => other.skillId !== item.skillId),
                )
              }
              aria-label={`Remove competitor ${item.name}`}
            >
              Remove
            </Button>
          </Stack>
        ))}
        <Typography variant="s3" color="text.secondary">
          {value.length} of {limit} competitors selected. The target skill
          cannot also be a competitor.
        </Typography>
      </Stack>
    </Section>
  );
}
CompetitorPicker.propTypes = {
  skillId: PropTypes.string.isRequired,
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  limit: PropTypes.number.isRequired,
  disabled: PropTypes.bool,
};

const errorCopy = (error) => {
  switch (getSkillErrorCode(error)) {
    case "draft_conflict":
      return "Your draft has a save conflict. Resolve it before testing; no different server revision was selected.";
    case "test_plan_changed":
      return "The draft, cases, baseline or model configuration changed. Preview again and review the updated plan.";
    case "test_input_unsafe":
      return "Outbound secret findings remain unresolved. Remove real secrets or review genuine false positives.";
    case "evaluation_material_present":
      return "Edit the real draft to remove evaluation material, or explicitly choose a diagnostic check and preview again.";
    case "spend_policy_required":
      return "An administrator must enable the workspace spending allowance before tests can run. Open Workspace allowance in Skills.";
    case "skills_capacity_exceeded":
      return "Workspace model capacity is busy. Your setup is preserved; retry after capacity becomes available.";
    case "model_not_configured":
      return "Connect a supported workspace model to run tests. There is no fallback connection.";
    case "model_tools_unsupported":
      return "This model transport cannot run the required read-only tools. Choose a supported model.";
    case "model_pricing_unavailable":
      return "This model has no supported price estimate. Choose another configured model.";
    case "principal_not_supported":
      return "Use a real user account with workspace write access to run private tests.";
    default:
      return "Could not confirm this request. Your setup is preserved. Retrying a submitted run uses the same idempotency key.";
  }
};

export default function TestRunSetup({
  skill,
  draft,
  candidate,
  suite,
  caseIds,
  onBeforePreview,
  onStarted,
  compact = false,
  parentRunId = null,
}) {
  const options = useSkillTestingOptions();
  const { scope } = useSkillV11Scope();
  const previewMutation = usePreviewSkillTestRun(skill.id);
  const create = useCreateSkillTestRun(skill.id);
  const production = skill.labels.find((label) => label.name === "production");
  const [settings, setSettings] = useState({
    profile: "quick",
    baselines: production ? ["production"] : [],
    modelId: "",
    judgeId: "",
    temperature: "0",
    maxOutputTokens: "",
    maxTotalTokens: "",
    cost: "",
    competitors: [],
    purpose: parentRunId ? "diagnostic" : "regression",
  });
  const [plan, setPlan] = useState(null);
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const submission = useRef(null);
  const actionBusy = useRef(false);
  const sourceKey = JSON.stringify({
    files: candidate ? null : draft?.files,
    candidate,
    suiteRevision: suite.revision,
    caseIds,
  });
  const currentSource = useRef(sourceKey);
  currentSource.current = sourceKey;
  const selectedCases = suite.cases.filter((item) => caseIds.includes(item.id));
  const needsJudge = selectedCases.some((item) =>
    item.checks.some((check) => check.kind === "semantic"),
  );
  const limits = options.data?.limits;
  const profileLimits = limits?.profiles[settings.profile];
  const model = options.data?.models.find(
    (item) => item.modelId === settings.modelId,
  );
  const judge = options.data?.models.find(
    (item) => item.modelId === settings.judgeId,
  );
  const outputTokens =
    settings.maxOutputTokens === ""
      ? profileLimits?.maxTaskOutputTokens
      : Number(settings.maxOutputTokens);
  const totalTokens =
    settings.maxTotalTokens === ""
      ? profileLimits?.maxTotalTokens
      : Number(settings.maxTotalTokens);
  const permitted = skill.permissions?.canRunTests === true;
  // Paid runs need a workspace allowance. It is read before consent so a missing allowance is a
  // calm prerequisite beside the run, never an error after pressing it. Consent stays separate.
  const spend = useSkillsSpendPolicy();
  const access = useSkillsAccess();
  const spendRefused = error.includes("administrator must enable");
  // Mirrors the server's admission rule for text runs: they run unless an admin has turned the
  // workspace allowance off. With no allowance yet the server opens an unlimited one, and unlike
  // application runs a text run needs no monthly limit.
  const policyOff = Boolean(spend?.data) && spend.data.enabled === false;
  // A refusal while the policy is unknown also disables the run; once the re-read policy says an
  // allowance exists, the reviewer may retry and the server checks again.
  const allowanceOff = policyOff || (spendRefused && !spend?.data);
  // The send tick (files, answer keys and provider processing in one, UXF-03) covers this run's spend,
  // so it waits for an allowance: while paid runs are off it is disabled and cleared, and it comes back
  // unticked once an admin sets one. A submission already sent (an uncertain outcome) keeps its tick, so
  // "Retry same submission" is unchanged.
  const consentHeld = allowanceOff && !submitted;
  const ticked = Boolean(review?.review.reviewedAllFiles);
  useEffect(() => {
    if (consentHeld && ticked)
      setReview((previous) => previous && {
        ...previous,
        review: { ...previous.review, reviewedAllFiles: false, noKnownAnswerKeys: false },
        complete: false,
      });
  }, [consentHeld, ticked]);
  // The tick counts only for the preview it was given for.
  const sendReady = Boolean(plan && review?.complete && review.review.fingerprint === plan.preview.fingerprint);
  const valid =
    permitted &&
    model?.canRun &&
    (!needsJudge || judge?.canRun) &&
    selectedCases.length > 0 &&
    selectedCases.length === caseIds.length &&
    selectedCases.every((item) => item.reviewed) &&
    selectedCases.length <= profileLimits?.maxCases &&
    settings.baselines.length > 0 &&
    settings.baselines.length + 1 <= profileLimits?.maxVariants &&
    Number.isInteger(outputTokens) &&
    outputTokens > 0 &&
    outputTokens <= profileLimits?.maxTaskOutputTokens &&
    Number.isInteger(totalTokens) &&
    totalTokens > 0 &&
    totalTokens <= profileLimits?.maxTotalTokens &&
    Number.isFinite(Number(settings.temperature)) &&
    Number(settings.temperature) >= 0 &&
    Number(settings.temperature) <= 2 &&
    /^\d+(?:\.\d{1,8})?$/.test(settings.cost) &&
    Number(settings.cost) > 0 &&
    Number(settings.cost) <= Number(limits?.maxEstimatedCostUsd) &&
    settings.competitors.every((item) => item.skillId !== skill.id);
  const locked = busy || submitted;
  const stale = plan && plan.sourceKey !== sourceKey;
  const change = (patch) => {
    if (locked) return;
    setSettings((previous) => ({ ...previous, ...patch }));
    setPlan(null);
    setReview(null);
    setError("");
    submission.current = null;
  };
  const preview = async () => {
    if (!valid || actionBusy.current) return;
    actionBusy.current = true;
    setBusy(true);
    setError("");
    setPlan(null);
    setReview(null);
    let basis = sourceKey;
    try {
      const saved = onBeforePreview ? await onBeforePreview() : draft;
      if (!candidate && (!saved || saved.revision < 1)) {
        setError(
          "Save your draft and resolve any conflict before testing. No run was started.",
        );
        return;
      }
      const resolvedCandidate = candidate ?? (await draftCandidate(saved));
      if (!candidate)
        basis = JSON.stringify({
          files: saved.files,
          candidate,
          suiteRevision: suite.revision,
          caseIds,
        });
      if (currentSource.current !== basis) {
        setError(
          "The editor or selected examples changed while preparing the test. Preview again so the newest edits are included.",
        );
        return;
      }
      const configuration = {
        candidate: resolvedCandidate,
        cases: { kind: "saved", suiteRevision: suite.revision, caseIds },
        baselines: settings.baselines,
        competitors: settings.competitors.map(({ skillId, version }) => ({
          skillId,
          version,
        })),
        model: {
          modelId: settings.modelId,
          temperature: Number(settings.temperature),
          maxOutputTokens: outputTokens,
        },
        judge: needsJudge
          ? {
              modelId: settings.judgeId,
              temperature: 0,
              maxOutputTokens: profileLimits.maxJudgeOutputTokens,
            }
          : null,
        profile: settings.profile,
        repetitions: profileLimits.repetitions,
        limits: {
          maxTotalTokens: totalTokens,
          maxEstimatedCostUsd: settings.cost,
        },
        purpose: settings.purpose,
        parentRunId,
      };
      const result = await previewMutation.mutateAsync(configuration);
      setPlan({ configuration, preview: result, sourceKey: basis });
      setReview({
        review: {
          fingerprint: result.fingerprint,
          reviewedAllFiles: false,
          noKnownAnswerKeys: false,
          classifications: [],
          falsePositiveSecretFindings: [],
        },
        complete: false,
        diagnostic: true,
      });
      submission.current = null;
    } catch (failure) {
      setError(errorCopy(failure));
    } finally {
      actionBusy.current = false;
      setBusy(false);
    }
  };
  const start = async () => {
    if (
      !permitted ||
      actionBusy.current ||
      !plan ||
      (!submitted &&
        (stale ||
          !sendReady ||
          (review.diagnostic && settings.purpose !== "diagnostic")))
    )
      return;
    const request = {
      configuration: plan.configuration,
      expectedFingerprint: plan.preview.fingerprint,
      acknowledgeProviderProcessing: true,
      inventoryReview: review.review,
    };
    if (
      !submission.current ||
      JSON.stringify({ ...submission.current, idempotencyKey: undefined }) !==
        JSON.stringify(request)
    )
      submission.current = { ...request, idempotencyKey: crypto.randomUUID() };
    actionBusy.current = true;
    setBusy(true);
    setSubmitted(true);
    setError("");
    try {
      const run = await create.mutateAsync(submission.current);
      onStarted(run, plan.configuration);
    } catch (failure) {
      setError(errorCopy(failure));
      if (getSkillErrorCode(failure) === "spend_policy_required") spend?.refetch?.();
      // A validation rejection did not admit work. Unknown/network outcomes keep the exact submission for replay.
      const capacityRejected = !submitted && operationStatus(failure) === 429 && getSkillErrorCode(failure) === "skills_capacity_exceeded";
      if (capacityRejected) setSubmitted(false);
      const confirmedRejected = !submitted && isDefiniteRejection(failure);
      if (confirmedRejected) {
        setSubmitted(false);
        submission.current = null;
      }
      if (
        confirmedRejected && ["test_plan_changed", "candidate_changed", "draft_conflict"].includes(
          getSkillErrorCode(failure),
        )
      ) {
        setSubmitted(false);
        submission.current = null;
        setPlan(null);
        setReview(null);
      }
    } finally {
      actionBusy.current = false;
      setBusy(false);
    }
  };
  if (options.isLoading)
    return (
      <ContentSkeleton variant="rows" rows={3} label="Loading test options" />
    );
  if (options.isError)
    return (
      <ErrorState
        title="Could not load test options"
        onRetry={options.refetch}
      />
    );
  if (!options.data?.enabled || !profileLimits)
    return (
      <Alert severity="info">
        Model tests are not available in this deployment. Saved examples remain
        available.
      </Alert>
    );
  // The same names and order as the context answer check (UXF-03, UXF-14): one name for the test,
  // the model and cost ceiling, what to compare with, then "Preview answer check". Profile, model
  // parameters, how the result may be used and other skills to compare are under More options.
  const testName = settings.profile === "quick" ? "answer check" : "repeated answer check";
  const providers = plan
    ? [plan.preview.providerDisclosure.taskProvider, plan.preview.providerDisclosure.judgeProvider]
        .filter(Boolean)
        .map(providerName)
        .filter((name, index, all) => all.indexOf(name) === index)
    : [];
  const fileCount = plan ? plan.preview.inventory.reduce((total, bundle) => total + bundle.files.length, 0) : 0;
  const runReason = !submitted && allowanceOff
    ? "Paid runs are off for this workspace."
    : !submitted && plan && !stale && !sendReady
      ? (review?.review.reviewedAllFiles ? "Classify the flagged files and check any secret findings to run." : "Tick the box above to run.")
      : null;
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Typography variant="s1">
        {candidate
          ? `Testing v${candidate.version}`
          : `Testing your saved draft (revision ${draft?.revision ?? 0})`}{" "}
        · {plural(caseIds.length, "test case")}
      </Typography>
      <Typography variant="s3" color="text.secondary">
        Test cases run text only — no scripts or tools.
      </Typography>
      {!permitted && (
        <Alert severity="info">
          Workspace write access is required to run tests.
        </Alert>
      )}
      {(policyOff || spendRefused) && (
        <CapabilityNotice
          title="Paid runs need a workspace allowance"
          description={
            access.canAdmin
              ? "Set a limit for paid test runs in this workspace, then run this test."
              : "Ask a workspace admin to set an allowance."
          }
          actions={
            access.canAdmin ? (
              <Button
                variant="outlined"
                size="small"
                href={allowanceHref()}
              >
                Set allowance
              </Button>
            ) : (
              <AdminAllowanceRequest
                request={allowanceRequestText({
                  purpose: `a test of ${skill.name}`,
                  ceilingUsd: /^\d+(?:\.\d{1,8})?$/.test(settings.cost) ? settings.cost : null,
                  href: allowanceHref(),
                })}
              />
            )
          }
        />
      )}
      {error && !spendRefused && <Alert severity="error">{error}</Alert>}
      {!options.data.models.some((item) => item.canRun) && (
        <Alert severity="info">
          Connect a supported workspace model to run tests.{" "}
          <Button
            size="small"
            href={`/dashboard/settings/workspace/${encodeURIComponent(scope.workspaceId)}/ai-providers`}
          >
            Open workspace model settings
          </Button>
        </Alert>
      )}
      <Box
        component="fieldset"
        disabled={locked || !permitted}
        sx={{ border: 0, m: 0, p: 0, minWidth: 0 }}
      >
        <Stack spacing={2}>
          <Stack direction={{ xs: "column", sm: compact ? "column" : "row" }} spacing={1}>
            <TextField
              select
              size="small"
              label="Task model"
              value={settings.modelId}
              onChange={(event) => change({ modelId: event.target.value })}
              sx={{ minWidth: 200 }}
            >
              {options.data.models.map((item) => (
                <MenuItem
                  key={item.modelId}
                  value={item.modelId}
                  disabled={!item.canRun}
                >
                  {item.modelId}
                  {!item.canRun ? ` · ${item.disabledReason}` : ""}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="Maximum estimated cost (USD)"
              value={settings.cost}
              inputProps={{ inputMode: "decimal" }}
              helperText={<>Up to <Money value={limits.maxEstimatedCostUsd} round="down" />. A provider estimate, not an invoice.</>}
              onChange={(event) => change({ cost: event.target.value })}
            />
          </Stack>
          {needsJudge && (
            <TextField
              select
              size="small"
              label="AI judge for plain-language checks"
              value={settings.judgeId}
              helperText="A separately billed judge call at temperature 0."
              onChange={(event) => change({ judgeId: event.target.value })}
            >
              {options.data.models.map((item) => (
                <MenuItem
                  key={item.modelId}
                  value={item.modelId}
                  disabled={!item.canRun}
                >
                  {item.modelId}
                  {item.modelId === settings.modelId
                    ? " · use selected task model as judge"
                    : ""}
                </MenuItem>
              ))}
            </TextField>
          )}
          <FormControl component="fieldset" sx={{ minWidth: 0 }}>
            <FormLabel component="legend" sx={{ color: "text.primary", typography: "s1" }}>Compare with</FormLabel>
            {!production && (
              <Typography variant="s3" color="text.secondary">
                No production version yet. Compare without this skill instead.
              </Typography>
            )}
            <Stack direction={{ xs: "column", sm: "row" }} columnGap={2}>
              <FormControlLabel
                control={
                  <Checkbox
                    disabled={!production || locked}
                    checked={settings.baselines.includes("production")}
                    onChange={(event) =>
                      change({
                        baselines: event.target.checked
                          ? [...settings.baselines, "production"]
                          : settings.baselines.filter(
                              (item) => item !== "production",
                            ),
                      })
                    }
                  />
                }
                label={
                  production
                    ? `Production v${production.version}`
                    : "Production (not released yet)"
                }
              />
              <FormControlLabel
                control={
                  <Checkbox
                    disabled={locked}
                    checked={settings.baselines.includes("without_target")}
                    onChange={(event) =>
                      change({
                        baselines: event.target.checked
                          ? [...settings.baselines, "without_target"]
                          : settings.baselines.filter(
                              (item) => item !== "without_target",
                            ),
                      })
                    }
                  />
                }
                label={
                  settings.competitors.length
                    ? "Without this skill (the other chosen skills stay)"
                    : "Without this skill"
                }
              />
            </Stack>
          </FormControl>
          {settings.baselines.length + 1 > profileLimits.maxVariants && (
            <Alert severity="warning">
              An answer check compares with one version. Untick one, or choose
              Repeated answer check under More options.
            </Alert>
          )}
          {selectedCases.some((item) => !item.reviewed) && (
            <Alert severity="warning">
              Approve every selected test case before running.
            </Alert>
          )}
          {caseIds.length > profileLimits.maxCases && (
            <Alert severity="warning">
              Select at most {plural(profileLimits.maxCases, "test case")} for this test.
            </Alert>
          )}
          <TechnicalDetails title="More options">
            <Stack spacing={1.5}>
              <TextField
                select
                size="small"
                label="Test"
                value={settings.profile}
                onChange={(event) =>
                  change({
                    profile: event.target.value,
                    maxOutputTokens: "",
                    maxTotalTokens: "",
                  })
                }
              >
                <MenuItem value="quick">Answer check</MenuItem>
                <MenuItem value="repeated">Repeated answer check</MenuItem>
              </TextField>
              <Typography variant="s3" color="text.secondary">
                Up to {plural(profileLimits.maxCases, "test case")} ·{" "}
                {plural(profileLimits.repetitions, "attempt")} per version ·{" "}
                stops after {profileLimits.deadlineSeconds} seconds
              </Typography>
              <Stack
                direction={{ xs: "column", sm: compact ? "column" : "row" }}
                spacing={1.5}
              >
                <TextField
                  fullWidth
                  size="small"
                  type="number"
                  label="Task temperature"
                  value={settings.temperature}
                  inputProps={{ min: 0, max: 2, step: 0.1 }}
                  onChange={(event) =>
                    change({ temperature: event.target.value })
                  }
                />
                <TextField
                  fullWidth
                  size="small"
                  type="number"
                  label="Task output tokens per call"
                  value={outputTokens}
                  inputProps={{
                    min: 1,
                    max: profileLimits.maxTaskOutputTokens,
                  }}
                  onChange={(event) =>
                    change({ maxOutputTokens: event.target.value })
                  }
                />
              </Stack>
              <TextField
                size="small"
                type="number"
                label="Total token stop"
                value={totalTokens}
                inputProps={{ min: 1, max: profileLimits.maxTotalTokens }}
                onChange={(event) =>
                  change({ maxTotalTokens: event.target.value })
                }
              />
              {/* The run's purpose decides whether the send tick attests "no answer keys"; it is kept,
                  in plain words, because a flagged file can only run as a diagnostic. */}
              <TextField
                select
                size="small"
                label="Use of the result"
                value={settings.purpose}
                disabled={Boolean(parentRunId)}
                onChange={(event) => change({ purpose: event.target.value })}
              >
                <MenuItem value="regression">
                  Compare versions (can support a release)
                </MenuItem>
                <MenuItem value="diagnostic">
                  Diagnostic only (no improvement claim)
                </MenuItem>
              </TextField>
              <CompetitorPicker
                skillId={skill.id}
                value={settings.competitors}
                onChange={(competitors) => change({ competitors })}
                limit={limits.maxCompetitors}
                disabled={locked}
              />
            </Stack>
          </TechnicalDetails>
        </Stack>
      </Box>
      {parentRunId && (
        <Alert severity="info">
          This is a diagnostic child run. It does not replace or complete the
          original evidence.
        </Alert>
      )}
      <Box>
        <Button
          color="primary"
          variant={plan ? "outlined" : "contained"}
          disabled={!valid || locked}
          onClick={preview}
        >
          {busy && !submitted ? "Preparing preview…" : `Preview ${testName}`}
        </Button>
      </Box>
      {plan && (
        <Stack spacing={1.5}>
          <Typography variant="m3">
            Up to {plural(plan.preview.attemptCount, "run")} · at most{" "}
            <Money value={plan.preview.estimate.upperBoundUsd} round="up" />
          </Typography>
          <Typography variant="s3">
            Test runs are private and don’t change production.{" "}
            {providers.length > 1
              ? `The providers, ${providers.join(" and ")}, receive`
              : `The task provider, ${providers[0]}, receives`}{" "}
            the files and test cases below. The run stops if its estimate
            reaches <Money value={plan.configuration.limits.maxEstimatedCostUsd} round="up" />.
          </Typography>
          <TechnicalDetails title="Technical details">
            <Stack spacing={0.5}>
              <Typography variant="s3" color="text.secondary">
                {plural(plan.preview.caseCount, "test case")} ×{" "}
                {plural(plan.preview.variantCount, "version")} ×{" "}
                {plural(plan.preview.repetitions, "attempt")} per version ={" "}
                {plural(plan.preview.attemptCount, "attempt")}
              </Typography>
              <Typography variant="s3" color="text.secondary">
                At most {plan.preview.maxTaskCalls} task calls and{" "}
                {plan.preview.maxJudgeCalls} judge calls · task provider{" "}
                {plan.preview.providerDisclosure.taskProvider}
                {plan.preview.providerDisclosure.judgeProvider
                  ? ` · judge provider ${plan.preview.providerDisclosure.judgeProvider}`
                  : ""}
              </Typography>
              <Typography variant="s3" color="text.secondary">
                Upper-bound estimate: USD{" "}
                {plan.preview.estimate.upperBoundUsd} · stop: USD{" "}
                {plan.configuration.limits.maxEstimatedCostUsd}
              </Typography>
              <Typography variant="s3" color="text.secondary">
                Price source: {plan.preview.estimate.pricingSource}. Uses your
                workspace model connection; provider billing may differ from
                this estimate.
              </Typography>
              <Typography variant="s3" color="text.secondary">
                Task phase{" "}
                {plan.preview.limits.profiles[settings.profile].taskPhaseSeconds}
                s · judge phase{" "}
                {plan.preview.limits.profiles[settings.profile].judgePhaseSeconds}
                s · provider call{" "}
                {plan.preview.limits.profiles[settings.profile].providerCallSeconds}
                s maximum
              </Typography>
              <Typography variant="s3" color="text.secondary">
                {plan.preview.limits.profiles[settings.profile].maxTaskTurnsPerAttempt}{" "}
                task turns and{" "}
                {plan.preview.limits.profiles[settings.profile].maxToolCallsPerAttempt}{" "}
                tool calls per attempt ·{" "}
                {plan.preview.limits.profiles[settings.profile].maxDeliveredBytesPerAttempt}{" "}
                delivered UTF-8 bytes per attempt
              </Typography>
              <Typography variant="s3" color="text.secondary">
                {plan.preview.limits.maxActiveAttemptsPerRun} active attempts
                per run ·{" "}
                {plan.preview.limits.maxActiveProviderCallsPerOrganization}{" "}
                active provider calls and{" "}
                {plan.preview.limits.maxActiveModelJobsPerOrganization} model
                jobs per organization ·{" "}
                {plan.preview.limits.maxQueueWaitSeconds}s maximum queue wait
              </Typography>
            </Stack>
          </TechnicalDetails>
          {plan.preview.warnings.map((warning, index) => (
            <Alert key={`${warning.code}:${index}`} severity="warning">
              {warning.message}
            </Alert>
          ))}
          {stale && (
            <Alert severity="warning">
              For earlier content. Preview again before starting.
            </Alert>
          )}
          {review.review.reviewedAllFiles && review.diagnostic && settings.purpose !== "diagnostic" && (
            <Alert severity="warning">
              A flagged file is marked as evaluation material or overlaps a test
              case, so this can only run as a diagnostic. Edit the draft, or set
              Use of the result to Diagnostic only under More options and
              preview again.
            </Alert>
          )}
          <TestInventoryReview
            preview={plan.preview}
            value={review.review}
            onChange={setReview}
            disabled={locked}
            consentHeading="Before this runs"
            sendLabel={sendSentence({
              fileCount,
              what: plural(plan.preview.caseCount, "test case"),
              provider: providers.join(" and "),
              upTo: centsUp(plan.preview.estimate.upperBoundUsd),
              attestsNoAnswerKeys: settings.purpose !== "diagnostic",
            })}
            attestsNoAnswerKeys={settings.purpose !== "diagnostic"}
            consentDisabled={consentHeld}
            consentReason={consentHeld ? "Set an allowance first" : null}
          />
          {submitted && error && (
            <Alert severity="warning">
              The request may already have been accepted. Retry this same
              submission; do not start a new paid run to discover its status.
            </Alert>
          )}
          <Stack
            direction={{ xs: "column", sm: "row" }}
            spacing={1.5}
            alignItems={{ xs: "flex-start", sm: "center" }}
          >
            <Button
              variant={compact ? "outlined" : "contained"}
              color="primary"
              disabled={
                !permitted ||
                busy ||
                (!submitted &&
                  (allowanceOff ||
                    stale ||
                    !sendReady ||
                    (review.diagnostic && settings.purpose !== "diagnostic")))
              }
              {...reasonProps("skill-run-reason", runReason)}
              onClick={start}
            >
              {busy && submitted
                ? "Starting…"
                : submitted
                  ? "Retry same submission"
                  : `Run ${testName}`}
            </Button>
            <ActionReason id="skill-run-reason" reason={runReason} />
          </Stack>
        </Stack>
      )}
    </Stack>
  );
}
TestRunSetup.propTypes = {
  skill: PropTypes.object.isRequired,
  draft: PropTypes.object,
  candidate: PropTypes.object,
  suite: PropTypes.object.isRequired,
  caseIds: PropTypes.array.isRequired,
  onBeforePreview: PropTypes.func,
  onStarted: PropTypes.func.isRequired,
  compact: PropTypes.bool,
  parentRunId: PropTypes.string,
};
