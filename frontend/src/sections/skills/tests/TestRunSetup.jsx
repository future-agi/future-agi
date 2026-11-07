import { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Pagination,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { getSkillErrorCode, useSkillsList } from "src/api/skills";
import {
  useCreateSkillTestRun,
  usePreviewSkillTestRun,
  useSkillTestingOptions,
  useSkillV11Scope,
} from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import TestInventoryReview from "./TestInventoryReview";

async function draftCandidate(draft) {
  const sha = async (text) =>
    Array.from(
      new Uint8Array(
        await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
      ),
      (byte) => byte.toString(16).padStart(2, "0"),
    ).join("");
  const manifest = await Promise.all(
    draft.files.map(async (file) => [file.path, await sha(file.content)]),
  );
  // Match Python path ordering and json.dumps(ensure_ascii=True), including astral paths.
  manifest.sort(([left], [right]) => {
    const a = Array.from(left, (char) => char.codePointAt(0));
    const b = Array.from(right, (char) => char.codePointAt(0));
    for (let index = 0; index < Math.min(a.length, b.length); index += 1)
      if (a[index] !== b[index]) return a[index] - b[index];
    return a.length - b.length;
  });
  const canonical = JSON.stringify(manifest).replace(
    /[\u007f-￿]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
  return {
    kind: "draft",
    revision: draft.revision,
    digest: `sha256:${await sha(canonical)}`,
  };
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
      title="Selected competing skills"
      description="Pins the chosen production versions. No other workspace skills are silently included."
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
  const [acknowledged, setAcknowledged] = useState(false);
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
    setAcknowledged(false);
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
      setAcknowledged(false);
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
          !review?.complete ||
          !acknowledged ||
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
      // A validation rejection did not admit work. Unknown/network outcomes keep the exact submission for replay.
      const status = failure?.response?.status;
      if (status >= 400 && status < 500 && ![408, 409].includes(status))
        setSubmitted(false);
      if (
        ["test_plan_changed", "candidate_changed", "draft_conflict"].includes(
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
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Typography variant="m3">
        {candidate
          ? `Version v${candidate.version}`
          : `Your saved draft r${draft?.revision ?? 0}`}{" "}
        · {caseIds.length} selected examples
      </Typography>
      <Typography variant="s3" color="text.secondary">
        In this text-only test · no scripts or external actions
      </Typography>
      {!permitted && (
        <Alert severity="info">
          Workspace write access is required to run tests.
        </Alert>
      )}
      {error && <Alert severity="error">{error}</Alert>}
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
          <TextField
            select
            size="small"
            label="Comparison profile"
            value={settings.profile}
            onChange={(event) =>
              change({
                profile: event.target.value,
                maxOutputTokens: "",
                maxTotalTokens: "",
              })
            }
          >
            <MenuItem value="quick">Quick check</MenuItem>
            <MenuItem value="repeated">Repeated comparison</MenuItem>
          </TextField>
          <Typography variant="s3" color="text.secondary">
            Up to {profileLimits.maxCases} examples ·{" "}
            {profileLimits.repetitions} attempt(s) per arm ·{" "}
            {profileLimits.deadlineSeconds} second stop ceiling
          </Typography>
          {!production && (
            <Alert severity="info">
              No production version yet. Compare without this skill instead.
            </Alert>
          )}
          <Stack>
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
                  ? `Compare with production v${production.version}`
                  : "Production unavailable"
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
                  ? "Compare without this skill (keep selected competitors)"
                  : "Compare without this skill · without any skills"
              }
            />
          </Stack>
          {settings.baselines.length + 1 > profileLimits.maxVariants && (
            <Alert severity="warning">
              Choose exactly one baseline for a quick check, or choose repeated
              comparison.
            </Alert>
          )}
          {selectedCases.some((item) => !item.reviewed) && (
            <Alert severity="warning">
              Review every selected example before running.
            </Alert>
          )}
          {caseIds.length > profileLimits.maxCases && (
            <Alert severity="warning">
              Select at most {profileLimits.maxCases} examples for this profile.
            </Alert>
          )}
          <TextField
            select
            size="small"
            label="Task model"
            value={settings.modelId}
            onChange={(event) => change({ modelId: event.target.value })}
          >
            {options.data.models.map((item) => (
              <MenuItem
                key={item.modelId}
                value={item.modelId}
                disabled={!item.canRun}
              >
                {item.modelId}
                {!item.canRun ? ` — ${item.disabledReason}` : ""}
              </MenuItem>
            ))}
          </TextField>
          {needsJudge && (
            <TextField
              select
              size="small"
              label="AI judge for plain-language checks"
              value={settings.judgeId}
              helperText="An explicit, separately billed judge call. Temperature 0; output allowance shown in preview."
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
          <Box component="details">
            <Typography component="summary" variant="s3">
              Model parameters and token stop
            </Typography>
            <Stack spacing={1.5} sx={{ mt: 1.5 }}>
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
            </Stack>
          </Box>
          <TextField
            size="small"
            label="Estimated-cost stop (USD)"
            value={settings.cost}
            inputProps={{ inputMode: "decimal" }}
            helperText={`Enter a positive amount up to USD ${limits.maxEstimatedCostUsd}. This is a provider estimate, not an invoice.`}
            onChange={(event) => change({ cost: event.target.value })}
          />
          <TextField
            select
            size="small"
            label="Evidence purpose"
            value={settings.purpose}
            disabled={Boolean(parentRunId)}
            onChange={(event) => change({ purpose: event.target.value })}
          >
            <MenuItem value="regression">
              Reviewed regression comparison
            </MenuItem>
            <MenuItem value="diagnostic">
              Diagnostic · no independent improvement claim
            </MenuItem>
          </TextField>
          <Box component="details">
            <Typography component="summary" variant="s3">
              {settings.competitors.length} selected competitors · manage
              comparison context
            </Typography>
            <Box sx={{ mt: 1.5 }}>
              <CompetitorPicker
                skillId={skill.id}
                value={settings.competitors}
                onChange={(competitors) => change({ competitors })}
                limit={limits.maxCompetitors}
                disabled={locked}
              />
            </Box>
          </Box>
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
          variant="outlined"
          disabled={!valid || locked}
          onClick={preview}
        >
          {busy && !submitted ? "Preparing preview…" : "Preview work and spend"}
        </Button>
      </Box>
      {plan && (
        <Section title="Confirm the reviewed plan">
          <Stack spacing={2}>
            <Typography variant="s1">
              {plan.preview.caseCount} examples × {plan.preview.variantCount}{" "}
              arms × {plan.preview.repetitions} repeats ={" "}
              {plan.preview.attemptCount} attempts
            </Typography>
            <Typography variant="s3" color="text.secondary">
              At most {plan.preview.maxTaskCalls} task calls and{" "}
              {plan.preview.maxJudgeCalls} judge calls · task provider{" "}
              {plan.preview.providerDisclosure.taskProvider}
              {plan.preview.providerDisclosure.judgeProvider
                ? ` · judge provider ${plan.preview.providerDisclosure.judgeProvider}`
                : ""}
            </Typography>
            <Typography variant="s1">
              Upper-bound estimate USD {plan.preview.estimate.upperBoundUsd} ·
              stop at USD {plan.configuration.limits.maxEstimatedCostUsd}
            </Typography>
            <Typography variant="s3" color="text.secondary">
              Uses your workspace model connection. Provider billing may differ
              from this estimate. Price source:{" "}
              {plan.preview.estimate.pricingSource}.
            </Typography>
            <Box component="details">
              <Typography component="summary" variant="s3">
                Effective execution limits
              </Typography>
              <Typography variant="s3" component="p">
                Task phase{" "}
                {
                  plan.preview.limits.profiles[settings.profile]
                    .taskPhaseSeconds
                }
                s · judge phase{" "}
                {
                  plan.preview.limits.profiles[settings.profile]
                    .judgePhaseSeconds
                }
                s · provider call{" "}
                {
                  plan.preview.limits.profiles[settings.profile]
                    .providerCallSeconds
                }
                s maximum
              </Typography>
              <Typography variant="s3" component="p">
                {
                  plan.preview.limits.profiles[settings.profile]
                    .maxTaskTurnsPerAttempt
                }{" "}
                task turns and{" "}
                {
                  plan.preview.limits.profiles[settings.profile]
                    .maxToolCallsPerAttempt
                }{" "}
                tool calls per attempt ·{" "}
                {
                  plan.preview.limits.profiles[settings.profile]
                    .maxDeliveredBytesPerAttempt
                }{" "}
                delivered UTF-8 bytes per attempt
              </Typography>
              <Typography variant="s3" component="p">
                {plan.preview.limits.maxActiveAttemptsPerRun} active attempts
                per run ·{" "}
                {plan.preview.limits.maxActiveProviderCallsPerOrganization}{" "}
                active provider calls and{" "}
                {plan.preview.limits.maxActiveModelJobsPerOrganization} model
                jobs per organization ·{" "}
                {plan.preview.limits.maxQueueWaitSeconds}s maximum queue wait
              </Typography>
            </Box>
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
            <TestInventoryReview
              preview={plan.preview}
              value={review.review}
              onChange={setReview}
              disabled={locked}
            />
            {review.diagnostic && settings.purpose !== "diagnostic" && (
              <Alert severity="warning">
                Confirm there are no known answer keys, or choose Diagnostic and
                preview again.
              </Alert>
            )}
            <FormControlLabel
              control={
                <Checkbox
                  disabled={locked}
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
              }
              label="Send the reviewed files and example inputs to these model providers within this spend stop"
            />
            {submitted && error && (
              <Alert severity="warning">
                The request may already have been accepted. Retry this exact
                submission; do not start a new paid run to discover its status.
              </Alert>
            )}
            <Box>
              <Button
                variant={compact ? "outlined" : "contained"}
                disabled={
                  !permitted ||
                  busy ||
                  (!submitted &&
                    (stale ||
                      !review.complete ||
                      !acknowledged ||
                      (review.diagnostic && settings.purpose !== "diagnostic")))
                }
                onClick={start}
              >
                {busy && submitted
                  ? "Starting…"
                  : submitted
                    ? "Retry same submission"
                    : settings.profile === "quick"
                      ? "Run quick check"
                      : "Run repeated comparison"}
              </Button>
            </Box>
          </Stack>
        </Section>
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
