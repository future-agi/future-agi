import { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import { addIcon } from "@iconify/react";
import {
  Alert,
  Box,
  Button,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import { getSkillErrorCode } from "src/api/skills";
import { useSkillTestAttempt, useSkillTestFile } from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import HumanCheckReviewController from "../review/HumanCheckReviewController";

// Official Solar geometry, registered locally so evidence can always be closed offline.
addIcon("solar:close-linear", {
  width: 24,
  height: 24,
  body: '<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M19.0068 5L5.00684 19"/><path d="M19 19L5 5"/></g>',
});

const textSx = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", m: 0 };
const armName = (variant) =>
  ({
    candidate: "Candidate",
    production: "Production",
    without_target: "Without this skill",
  })[variant] ?? variant;

function SnapshotFile({ skillId, runId, receipt, variant, onClose }) {
  const [offset, setOffset] = useState(0);
  const query = useSkillTestFile(skillId, runId, {
    snapshotSkillId: receipt.skillId,
    variant,
    path: receipt.path,
    offset,
  });
  const chunk = query.data;
  const mismatch =
    chunk &&
    (chunk.digest !== receipt.digest ||
      chunk.sha256 !== receipt.sha256 ||
      chunk.skillId !== receipt.skillId ||
      chunk.path !== receipt.path ||
      chunk.offset !== offset);
  return (
    <Section
      title={`Snapshot file · ${receipt.path}`}
      actions={
        <Button size="small" onClick={onClose}>
          Close file
        </Button>
      }
    >
      {query.isLoading ? (
        <ContentSkeleton
          variant="rows"
          rows={3}
          label="Loading snapshot file"
        />
      ) : getSkillErrorCode(query.error) === "evidence_expired" ? (
        <Alert severity="info">
          Detailed evidence expired. This file can no longer be inspected.
        </Alert>
      ) : query.isError || mismatch ? (
        <ErrorState
          title="Could not verify this snapshot file"
          onRetry={query.refetch}
        />
      ) : (
        chunk && (
          <Stack spacing={1}>
            <Typography
              variant="s3"
              color="text.secondary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {chunk.digest} · SHA-256 {chunk.sha256} · character offset{" "}
              {chunk.offset} / {chunk.totalChars}
            </Typography>
            <Typography component="pre" variant="s1" sx={textSx}>
              {chunk.text}
            </Typography>
            <Stack direction="row" spacing={1}>
              <Button
                size="small"
                disabled={offset === 0}
                onClick={() => setOffset(0)}
              >
                First chunk
              </Button>
              <Button
                size="small"
                disabled={
                  chunk.eof ||
                  chunk.nextOffset === null ||
                  chunk.nextOffset <= offset
                }
                onClick={() => setOffset(chunk.nextOffset)}
              >
                Next chunk
              </Button>
            </Stack>
          </Stack>
        )
      )}
    </Section>
  );
}
SnapshotFile.propTypes = {
  skillId: PropTypes.string.isRequired,
  runId: PropTypes.string.isRequired,
  receipt: PropTypes.object.isRequired,
  variant: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};

function RequestContent({ testCase }) {
  return (
    <Section title="Request and supplied input">
      <Typography variant="s1" component="p" sx={textSx}>
        {testCase.prompt}
      </Typography>
      {testCase.fixtures.map((fixture) => (
        <Box key={fixture.id} sx={{ mt: 1.5 }}>
          <Typography variant="m3">{fixture.name}</Typography>
          <Typography component="pre" variant="s1" sx={textSx}>
            {fixture.text}
          </Typography>
        </Box>
      ))}
    </Section>
  );
}
RequestContent.propTypes = { testCase: PropTypes.object.isRequired };

function AttemptContent({
  skillId,
  runId,
  attemptId,
  onOpenFile,
  onImproveSelection,
  showRequest = false,
  refreshToken,
}) {
  const query = useSkillTestAttempt(skillId, runId, attemptId);
  const previousProgress = useRef(refreshToken);
  const refetch = query.refetch;
  useEffect(() => {
    if (previousProgress.current !== refreshToken && attemptId) refetch();
    previousProgress.current = refreshToken;
  }, [refreshToken, attemptId, refetch]);
  const [file, setFile] = useState(null);
  const [reviewCheckId, setReviewCheckId] = useState(null);
  if (query.isLoading)
    return (
      <ContentSkeleton
        variant="rows"
        rows={6}
        label="Loading attempt evidence"
      />
    );
  if (getSkillErrorCode(query.error) === "evidence_expired")
    return (
      <Alert severity="info">
        Detailed evidence expired. Hashes, configuration and summary counts
        remain.
      </Alert>
    );
  if (query.isError)
    return (
      <ErrorState title="Could not load this attempt" onRetry={query.refetch} />
    );
  if (!query.data)
    return (
      <EmptyState
        title="No attempt selected"
        description="Choose an attempt to inspect its answer and receipts."
      />
    );
  const attempt = query.data;
  const without = attempt.variant === "without_target";
  const routingMismatch =
    !without &&
    attempt.case.expectedTrigger !== null &&
    attempt.targetLoaded !== null &&
    attempt.case.expectedTrigger !== attempt.targetLoaded;
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      {showRequest && <RequestContent testCase={attempt.case} />}
      <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center">
        <Typography variant="m3">
          {armName(attempt.variant)} · attempt {attempt.repeatIndex + 1}
        </Typography>
        <StatusBadge status={attempt.status} />
      </Stack>
      {attempt.error && (
        <Alert severity="error">
          {attempt.error.code}: {attempt.error.message}
        </Alert>
      )}
      <Section title="Answer">
        <Typography component="pre" variant="s1" sx={textSx}>
          {attempt.answer === null
            ? "No answer available"
            : attempt.answer === ""
              ? "Empty answer"
              : attempt.answer}
        </Typography>
      </Section>
      <Section title="Expected, actual and evidence">
        <Stack spacing={1.5}>
          {!attempt.checks.length && (
            <Typography variant="s1" color="text.secondary">
              No answer assertions were graded. This is not an answer pass.
            </Typography>
          )}
          {attempt.checks.map((check) => (
            <Box
              key={check.checkId}
              sx={{ pb: 1.5, borderBottom: 1, borderColor: "divider" }}
            >
              <Stack
                direction="row"
                spacing={1}
                flexWrap="wrap"
                alignItems="center"
              >
                <Typography variant="s1">
                  {check.category} check
                  {attempt.case.checks.find((item) => item.id === check.checkId)
                    ?.critical
                    ? " · critical"
                    : ""}
                </Typography>
                <StatusBadge status={check.verdict} />
              </Stack>
              <Typography variant="s3" component="p" sx={textSx}>
                Expected: {JSON.stringify(check.expected)}
              </Typography>
              <Typography variant="s3" component="p" sx={textSx}>
                Actual: {JSON.stringify(check.actual)}
              </Typography>
              {check.evidence !== null && (
                <Typography
                  variant="s1"
                  component="blockquote"
                  sx={{ ...textSx, mt: 1 }}
                >
                  {check.evidence}
                </Typography>
              )}
              {check.reason && (
                <Typography variant="s3" color="text.secondary">
                  {check.reason}
                </Typography>
              )}
              <Button size="small" onClick={() => setReviewCheckId(reviewCheckId === check.checkId ? null : check.checkId)}>
                {reviewCheckId === check.checkId ? "Close human review" : "Review this check"}
              </Button>
              {reviewCheckId === check.checkId && <HumanCheckReviewController skillId={skillId} runId={runId} attemptId={attemptId} check={check} />}
            </Box>
          ))}
        </Stack>
      </Section>
      <Stack direction="row" spacing={1} flexWrap="wrap">
        {routingMismatch && onImproveSelection && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => onImproveSelection(attempt.case.id)}
          >
            Improve when this skill is used
          </Button>
        )}
        {onOpenFile && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => onOpenFile("SKILL.md")}
          >
            Open instructions
          </Button>
        )}
      </Stack>
      <Section title="Available → Requested → Root delivered → References delivered → Answer → Checks">
        <Stack spacing={0.5}>
          <Typography variant="s1">
            Target available:{" "}
            {without ? "No (control arm)" : "Yes, pinned snapshot"}
          </Typography>
          <Typography variant="s1">
            Target requested:{" "}
            {without
              ? "Not applicable"
              : attempt.targetRequested
                ? "Yes"
                : "No"}
          </Typography>
          <Typography variant="s1">
            {without
              ? "Selection: Not applicable"
              : attempt.targetLoaded === null
                ? "Selection unknown"
                : attempt.targetLoaded
                  ? "Target root delivered"
                  : "Target root not delivered"}
          </Typography>
          <Typography variant="s3" color="text.secondary">
            Complete root:{" "}
            {without
              ? "Not applicable"
              : attempt.rootFullyDelivered === null
                ? "Unknown"
                : attempt.rootFullyDelivered
                  ? "Yes"
                  : "No / partial delivery"}
          </Typography>
        </Stack>
      </Section>
      <Section
        title="Delivery receipts"
        description="Delivered means supplied to the model, not proven to be followed."
      >
        <Stack spacing={1}>
          {!attempt.receipts.length && (
            <Typography variant="s1" color="text.secondary">
              No file-delivery receipts.
            </Typography>
          )}
          {attempt.receipts.map((receipt, index) => (
            <Box
              key={`${receipt.callId}:${receipt.toolCallId}:${index}`}
              sx={{
                pb: 1,
                borderBottom: 1,
                borderColor: "divider",
                overflowWrap: "anywhere",
              }}
            >
              <Stack
                direction="row"
                spacing={1}
                flexWrap="wrap"
                alignItems="center"
              >
                <Typography variant="s1">{receipt.path}</Typography>
                <StatusBadge status={receipt.outcome} />
              </Stack>
              <Typography variant="s3" component="p" color="text.secondary">
                {receipt.skillId === skillId ? "Target" : "Competitor"}{" "}
                {receipt.skillId} · {receipt.versionId ?? "draft snapshot"}
              </Typography>
              <Typography variant="s3" component="p" color="text.secondary">
                Characters {receipt.offset} →{" "}
                {receipt.nextOffset ?? receipt.totalChars ?? "unknown"} ·{" "}
                {receipt.deliveredBytes} UTF-8 bytes ·{" "}
                {receipt.eof ? "EOF" : "continuation"}
              </Typography>
              <Typography variant="s3" component="p" color="text.secondary">
                {receipt.digest} · SHA-256 {receipt.sha256 ?? "unavailable"}
              </Typography>
              {receipt.errorCode && (
                <Typography variant="s3">
                  Rejected: {receipt.errorCode}
                </Typography>
              )}
              <Stack direction="row" spacing={1}>
                <Button
                  size="small"
                  disabled={receipt.outcome !== "delivered"}
                  onClick={() => setFile(receipt)}
                >
                  Inspect snapshot file
                </Button>
                {onOpenFile && receipt.skillId === skillId && (
                  <Button size="small" onClick={() => onOpenFile(receipt.path)}>
                    Open current file
                  </Button>
                )}
              </Stack>
            </Box>
          ))}
        </Stack>
      </Section>
      {file && (
        <SnapshotFile
          key={`${file.skillId}:${file.path}:${file.digest}`}
          skillId={skillId}
          runId={runId}
          receipt={file}
          variant={attempt.variant}
          onClose={() => setFile(null)}
        />
      )}
      <Section title="Model calls and provider estimates">
        <Stack spacing={1.5}>
          {attempt.calls.map((call) => (
            <Box
              key={call.id}
              sx={{
                pb: 1,
                borderBottom: 1,
                borderColor: "divider",
                overflowWrap: "anywhere",
              }}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="s1">
                  {call.kind} call {call.sequence}
                </Typography>
                <StatusBadge status={call.state} />
              </Stack>
              <Typography variant="s3" component="p">
                Requested model: {call.requestedModel} · Returned model:{" "}
                {call.returnedModel ?? "Unknown"}
              </Typography>
              <Typography variant="s3" component="p" color="text.secondary">
                Tokens:{" "}
                {call.usage.knownTotalTokens === null
                  ? "Unknown"
                  : call.usage.knownTotalTokens}
                {!call.usage.complete ? " · partial usage" : ""} · known
                estimate:{" "}
                {call.cost.knownEstimateUsd === null
                  ? "Unknown"
                  : `USD ${call.cost.knownEstimateUsd}`}{" "}
                · reserved: USD {call.cost.reservedEstimateUsd}
              </Typography>
              <Typography variant="s3" component="p" color="text.secondary">
                {call.cost.unknownCalls} unknown calls ·{" "}
                {call.cost.pricingSource} · latency{" "}
                {call.latencyMs === null ? "Unknown" : `${call.latencyMs} ms`} ·
                finish {call.finishReason ?? "Unknown"}
              </Typography>
              {call.error && (
                <Typography variant="s3">
                  {call.error.code}: {call.error.message}
                </Typography>
              )}
              <Box component="details" sx={{ mt: 1 }}>
                <Typography component="summary" variant="s3">
                  Inspect assistant message and tool requests
                </Typography>
                <Typography component="pre" variant="s3" sx={textSx}>
                  {JSON.stringify(call.assistantMessage, null, 2)}
                </Typography>
              </Box>
            </Box>
          ))}
          <Typography variant="s3" color="text.secondary">
            Elapsed:{" "}
            {attempt.metrics.elapsedMs === null
              ? "Unknown"
              : `${attempt.metrics.elapsedMs} ms`}{" "}
            · provider duration:{" "}
            {attempt.metrics.providerDurationMs === null
              ? "Unknown"
              : `${attempt.metrics.providerDurationMs} ms`}
            . No hidden reasoning is retained.
          </Typography>
        </Stack>
      </Section>
    </Stack>
  );
}
AttemptContent.propTypes = {
  skillId: PropTypes.string.isRequired,
  runId: PropTypes.string.isRequired,
  attemptId: PropTypes.string,
  onOpenFile: PropTypes.func,
  onImproveSelection: PropTypes.func,
  showRequest: PropTypes.bool,
  refreshToken: PropTypes.string,
};

export default function TestAttemptDrawer({
  open,
  onClose,
  skillId,
  runId,
  caseResult,
  attemptId,
  onSelectAttempt,
  onPrevious,
  onNext,
  onOpenFile,
  onImproveSelection,
  refreshToken,
}) {
  const covered = useDrawerCovered(open);
  const titleId = useId();
  const [repeat, setRepeat] = useState(0);
  const selected = caseResult?.variants
    .flatMap((variant) => variant.attempts)
    .find((attempt) => attempt.id === attemptId);
  const selectedRepeat = selected ? selected.repeatIndex : repeat;
  const attempts =
    caseResult?.variants.map((variant) => ({
      variant: variant.variant,
      attempt: variant.attempts.find(
        (item) => item.repeatIndex === selectedRepeat,
      ),
    })) ?? [];
  const request = useSkillTestAttempt(
    skillId,
    runId,
    open ? attempts[0]?.attempt?.id : undefined,
  );
  const repeats = Math.max(
    1,
    ...(caseResult?.variants.map((variant) => variant.planned) ?? []),
  );
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        role: "dialog",
        "aria-modal": true,
        "aria-labelledby": titleId,
        sx: { width: { xs: "100%", md: 960 }, maxWidth: "100%" },
      }}
    >
      <Stack
        spacing={2}
        sx={{ p: { xs: 2, sm: 3 }, minWidth: 0 }}
        onKeyDown={(event) => {
          if (!event.altKey) return;
          if (event.key === "ArrowLeft" && onPrevious) {
            event.preventDefault();
            onPrevious();
          }
          if (event.key === "ArrowRight" && onNext) {
            event.preventDefault();
            onNext();
          }
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography id={titleId} variant="m1" component="h2" sx={{ flex: 1 }}>
            {caseResult?.title ?? "Attempt evidence"}
          </Typography>
          <IconButton onClick={onClose} aria-label="Close attempt evidence">
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <Button size="small" disabled={!onPrevious} onClick={onPrevious}>
            Previous example
          </Button>
          <Button size="small" disabled={!onNext} onClick={onNext}>
            Next example
          </Button>
          {repeats > 1 && (
            <TextField
              size="small"
              select
              label="Repeat"
              value={selectedRepeat}
              onChange={(event) => {
                setRepeat(Number(event.target.value));
                onSelectAttempt?.(null);
              }}
            >
              {Array.from({ length: repeats }, (_, index) => (
                <MenuItem key={index} value={index}>
                  Attempt {index + 1}
                </MenuItem>
              ))}
            </TextField>
          )}
        </Stack>
        {open && attempts.length > 0 && request.data && !request.isError && (
          <RequestContent testCase={request.data.case} />
        )}
        {open && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                md:
                  attempts.length > 1
                    ? `repeat(${attempts.length}, minmax(0, 1fr))`
                    : "minmax(0, 1fr)",
              },
              gap: 3,
              alignItems: "start",
            }}
          >
            {attempts.length ? (
              attempts.map(({ variant, attempt }) => (
                <Box key={variant} sx={{ minWidth: 0 }}>
                  {attempt ? (
                    <AttemptContent
                      key={attempt.id}
                      skillId={skillId}
                      runId={runId}
                      attemptId={attempt.id}
                      refreshToken={refreshToken}
                      onOpenFile={onOpenFile}
                      onImproveSelection={onImproveSelection}
                    />
                  ) : (
                    <Typography variant="s1">
                      {armName(variant)}: attempt not started
                    </Typography>
                  )}
                </Box>
              ))
            ) : (
              <AttemptContent
                key={attemptId}
                skillId={skillId}
                runId={runId}
                attemptId={attemptId}
                showRequest
                refreshToken={refreshToken}
                onOpenFile={onOpenFile}
                onImproveSelection={onImproveSelection}
              />
            )}
          </Box>
        )}
      </Stack>
    </Drawer>
  );
}
TestAttemptDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  skillId: PropTypes.string.isRequired,
  runId: PropTypes.string.isRequired,
  caseResult: PropTypes.object,
  attemptId: PropTypes.string,
  onSelectAttempt: PropTypes.func,
  onPrevious: PropTypes.func,
  onNext: PropTypes.func,
  onOpenFile: PropTypes.func,
  onImproveSelection: PropTypes.func,
  refreshToken: PropTypes.string,
};
