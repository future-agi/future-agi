import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import useMediaQuery from "@mui/material/useMediaQuery";

import { ConfirmDialog } from "src/components/custom-dialog";

import { REASON_MAX } from "../constants";
import { useDecision, useHubRules } from "../api/queries";
import {
  answerFlags,
  approveBlockers,
  changeRequestText,
  decisionPayload,
  flagGroups,
  isSubmitter,
  reasonProblem,
} from "../lib/decision";
import {
  DISCLOSURE_FIELDS,
  disclosureGaps,
  disclosureShapeErrors,
} from "../lib/disclosure";
import { checkTone } from "../lib/status";

import ChangeNotesEditor from "./ChangeNotesEditor";
import DisclosureEditor from "./DisclosureEditor";
import FlagGroups, { ReviewSection } from "./FlagGroups";
import HubIcon from "./HubIcon";
import { toneSx } from "./ToneChip";

const VISUALLY_HIDDEN = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

const MODES = [
  {
    value: "approve",
    label: "Approve and publish",
    testId: "hub-decision-approve-tab",
  },
  {
    value: "request_changes",
    label: "Request changes",
    testId: "hub-decision-changes-tab",
  },
  { value: "reject", label: "Reject", testId: "hub-decision-reject-tab" },
];

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function initialDisclosure(draft) {
  return Object.fromEntries(
    DISCLOSURE_FIELDS.map((field) => [field, draft?.[field] || ""]),
  );
}

// Without a page around it (a test, or a narrow embed) the form stacks its
// parts: the review sections, then the Decision card.
function stackedLayout({ main, side, bar }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {main}
      {side}
      {bar}
    </Box>
  );
}

function ProgressMark({ state }) {
  if (state === "todo") {
    return (
      <Box
        component="span"
        sx={{
          width: 12,
          height: 12,
          mx: "1px",
          flexShrink: 0,
          borderRadius: "50%",
          border: "1.5px solid",
          borderColor: "text.secondary",
        }}
      />
    );
  }
  return (
    <Box
      component="span"
      sx={{
        ...toneSx(state === "done" ? checkTone("pass") : checkTone("flag")),
        display: "inline-flex",
        flexShrink: 0,
      }}
    >
      <HubIcon name={state === "done" ? "check" : "alert"} size={14} />
    </Box>
  );
}

ProgressMark.propTypes = { state: PropTypes.string.isRequired };

// The Decision card's progress lines: each says what is done and what is left.
function progressItems({
  groups,
  answered,
  anyNo,
  checked,
  items,
  gaps,
  outOfBounds,
  edited,
  selfApproval,
}) {
  const lines = [];
  if (!groups) lines.push(["flags", "done", "Flag groups: none to answer"]);
  else
    lines.push([
      "flags",
      anyNo ? "attention" : answered === groups ? "done" : "todo",
      `Flag groups answered: ${answered} of ${groups}`,
    ]);
  if (!items)
    lines.push([
      "checklist",
      "todo",
      "Checklist: waiting for the review rules",
    ]);
  else
    lines.push([
      "checklist",
      checked === items ? "done" : "todo",
      `Checklist: ${checked} of ${items}`,
    ]);
  if (gaps)
    lines.push([
      "disclosure",
      "attention",
      `Disclosure: ${plural(gaps, "part")} to write`,
    ]);
  else if (outOfBounds)
    lines.push([
      "disclosure",
      "attention",
      `Disclosure: ${plural(outOfBounds, "part")} outside its length`,
    ]);
  else if (edited)
    lines.push([
      "disclosure",
      "done",
      `Disclosure: ${plural(edited, "part")} edited in review`,
    ]);
  else lines.push(["disclosure", "done", "Disclosure: as drafted"]);
  if (selfApproval)
    lines.push([
      "approver",
      "attention",
      "Approver: you submitted this version, so another maintainer approves it",
    ]);
  return lines.map(([key, state, text]) => ({ key, state, text }));
}

// The sticky bar's line on a narrow page: the decision being made, what is
// left to answer and tick, or, once every group is answered and every item
// ticked, why Approve is still off (the same reasons the Decision card gives).
function barLine({
  anyNo,
  decision,
  ready,
  groups,
  answered,
  checked,
  items,
  blockers,
}) {
  if (anyNo) return "A flag group is answered No";
  if (decision === "request_changes") return "Requesting changes";
  if (decision === "reject") return "Rejecting this submission";
  if (ready) return "Ready to approve";
  const progress = [
    groups ? `${answered} of ${plural(groups, "flag group")}` : "",
    items ? `${checked} of ${plural(items, "checklist item")}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  if (answered === groups && checked === items && blockers.length)
    return `To approve: ${blockers.join("; ")}`;
  return progress;
}

// The decision (SPEC §5.3, §5.4). The checklist comes from GET /rules; the
// form holds no rule text of its own. It lays out as the review's sections
// (flags grouped by rule, the disclosure, the checklist), the Decision card,
// and, on a page too narrow for the card to sit beside them, a sticky bar.
// Approve stays disabled until every flag group is answered Yes (which
// acknowledges each of its flags), every checklist item is ticked, every
// observed disclosure part is written, the changes and rules have loaded, and
// the reviewer is not the one who submitted the version. Any No makes Request
// changes the decision and drafts its reason; answering it Yes again while
// the draft is untouched makes Approve the decision again.
export default function DecisionForm({
  submission,
  onDecided,
  approveDisabled = false,
  diffError,
  diffLoading = false,
  onRetryDiff,
  viewer,
  onOpenFile,
  layout = stackedLayout,
}) {
  const {
    data: rules,
    refetch: reloadRules,
    isError: rulesError,
    isFetching: rulesFetching,
  } = useHubRules();
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("lg"));
  const headingId = useId();
  const modeId = useId();
  const barTextId = useId();
  const rulesStatus = useRef(null);
  const diffStatus = useRef(null);
  const reasonInput = useRef(null);
  const pendingReasonFocus = useRef(false);
  const prefill = useRef("");
  // True while Request changes is the decision because a No chose it, so a
  // later Yes can hand the decision back to Approve.
  const changesFromNo = useRef(false);
  const rulesUnavailable = !rules || rulesError;
  const diffBusy =
    (approveDisabled || diffError) && (diffLoading || !diffError);
  const decide = useDecision(submission.id);
  const [decision, setDecision] = useState("approve");
  const [confirming, setConfirming] = useState(false);
  const [acknowledged, setAcknowledged] = useState([]);
  const [answers, setAnswers] = useState({});
  const [checklist, setChecklist] = useState({});
  const [disclosure, setDisclosure] = useState(() =>
    initialDisclosure(submission.draftDisclosure),
  );
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState([]);
  const [announcement, setAnnouncement] = useState("");
  const flags = submission.flags || [];
  const groups = useMemo(
    () => flagGroups(submission.flags, submission.report),
    [submission.flags, submission.report],
  );
  const files = useMemo(
    () => new Set((submission.files || []).map((file) => file.path)),
    [submission.files],
  );
  const checklistItems = rules?.checklist || [];
  const rulesChanged = decide.error?.code === "OMEGA_HUB_RULES_CHANGED";
  const selfApproval = isSubmitter(viewer, submission.submittedBy);

  const blockers = approveBlockers({
    flags,
    acknowledged,
    checklistItems,
    checklist,
    disclosure,
    observed: submission.observed,
    groups,
    answers,
    selfApproval,
  });
  if (approveDisabled || diffError) blockers.push("the changes must load");
  if (rulesUnavailable) blockers.push("the review rules must load");
  const problem = decision === "approve" ? null : reasonProblem(reason);
  // Reject/request-changes depend on their reason, not on a successful diff.
  const canSubmit =
    decision === "approve"
      ? !approveDisabled && !diffError && blockers.length === 0
      : !problem;
  const disabled = !canSubmit || decide.isPending || rulesUnavailable;
  const ref = `${submission.publisher}/${submission.name || submission.hints?.name || submission.id}${submission.version ? `@${submission.version}` : ""}`;
  const decisionError = decide.isError && (
    <Alert severity="error" data-testid="hub-decision-error">
      {decide.error.message}
      {decide.error.fix ? ` ${decide.error.fix}` : ""}
    </Alert>
  );

  const answered = groups.filter((group) => answers[group.key]).length;
  const anyNo = groups.some((group) => answers[group.key] === "no");
  const checked = checklistItems.filter(
    (item) => checklist[item.key] === true,
  ).length;
  const gaps = disclosureGaps(disclosure, submission.observed);
  const outOfBounds = Object.keys(disclosureShapeErrors(disclosure));
  const edited = DISCLOSURE_FIELDS.filter(
    (key) =>
      (submission.draftDisclosure?.[key] || "") !== (disclosure[key] || ""),
  ).length;
  const progress = progressItems({
    groups: groups.length,
    answered,
    anyNo,
    checked,
    items: checklistItems.length,
    gaps: gaps.length,
    outOfBounds: outOfBounds.length,
    edited,
    selfApproval,
  });

  useEffect(() => {
    if (!pendingReasonFocus.current || !reasonInput.current) return;
    pendingReasonFocus.current = false;
    reasonInput.current.focus();
    reasonInput.current.scrollIntoView?.({ block: "center" });
  });

  // A No drafts the change request, one line per group answered No. The draft
  // follows the answers until the reviewer writes their own words.
  const syncPrefill = (nextAnswers) => {
    const text = changeRequestText(groups, nextAnswers);
    const previous = prefill.current;
    prefill.current = text;
    setReason((current) =>
      current.trim() === "" || current === previous ? text : current,
    );
  };
  const choose = (next) => {
    changesFromNo.current = false;
    setDecision(next);
    if (next === "request_changes") syncPrefill(answers);
    // A drafted change request is not a rejection reason: Reject starts empty
    // unless the reviewer wrote words of their own.
    if (next === "reject" && reason && reason === prefill.current)
      setReason("");
  };
  const answer = (key, value) => {
    const group = groups.find((item) => item.key === key);
    const next = { ...answers, [key]: value };
    setAnswers(next);
    setAcknowledged((list) =>
      answerFlags(list, group?.flags || [], value === "yes"),
    );
    if (value === "no" && decision === "approve") {
      changesFromNo.current = true;
      setDecision("request_changes");
      setAnnouncement(
        "A flag group is answered No, so the decision is now Request changes.",
      );
      syncPrefill(next);
      return;
    }
    if (decision !== "request_changes") return;
    // Read before the draft follows the new answers: true while the reviewer
    // has added no words or file notes of their own.
    const untouched = reason === prefill.current && notes.length === 0;
    syncPrefill(next);
    const stillNo = groups.some((item) => next[item.key] === "no");
    // The No that chose Request changes is answered Yes again: the decision
    // goes back to Approve, which keeps its own gate.
    if (changesFromNo.current && !stillNo && untouched) {
      changesFromNo.current = false;
      setDecision("approve");
      setAnnouncement(
        "No flag group is answered No now, so the decision is back to Approve and publish.",
      );
    }
  };
  const goToReason = () => {
    if (decision !== "approve" && reasonInput.current) {
      reasonInput.current.focus();
      reasonInput.current.scrollIntoView?.({ block: "center" });
      return;
    }
    pendingReasonFocus.current = true;
    choose("request_changes");
  };

  const submit = async () => {
    if (disabled) return;
    const payload = decisionPayload({
      decision,
      rulesVersion: rules?.version,
      reason,
      checklistItems,
      acknowledged,
      disclosure,
      notes,
    });
    try {
      const result = await decide.mutateAsync(payload);
      setConfirming(false);
      onDecided?.(result, decision);
    } catch {
      // The form shows decide.error (and the rules-changed state).
    }
  };

  const modeHint = {
    approve: `Publishes ${submission.version || "this version"} with the disclosure as shown.`,
    request_changes:
      "The submitter sees your reason and notes, and answers with a new upload.",
    reject:
      "Closes this submission for good. The submitter receives your reason by email.",
  };

  const main = (
    <>
      <FlagGroups
        groups={groups}
        answers={answers}
        onAnswer={answer}
        onOpenFile={onOpenFile}
        files={files}
      />
      <ReviewSection
        title="Disclosure"
        testId="hub-review-disclosure"
        description="Drafted by the publisher. Edit any part; approving confirms it, and it shows on the entry page."
      >
        <DisclosureEditor
          value={disclosure}
          onChange={setDisclosure}
          observed={submission.observed}
          draft={submission.draftDisclosure}
        />
      </ReviewSection>
      <ReviewSection
        title="Checklist"
        note={rules && !rulesChanged ? `rules version ${rules.version}` : null}
        testId="hub-review-checklist"
        description="Tick each item once you have checked it. Approving needs every item."
      >
        {rulesChanged ? (
          <Typography typography="s1" color="text.secondary">
            The Hub rules changed while you reviewed. Reload them in the
            Decision panel to see the current checklist.
          </Typography>
        ) : checklistItems.length === 0 ? (
          <Typography typography="s1" color="text.secondary">
            The checklist shows once the review rules load.
          </Typography>
        ) : (
          <Box
            data-testid="hub-checklist"
            sx={{
              display: "flex",
              flexDirection: "column",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
              px: 1.5,
              py: 0.5,
            }}
          >
            {checklistItems.map((item) => (
              <FormControlLabel
                key={item.key}
                sx={{ alignItems: "flex-start", m: 0, py: 0.75 }}
                control={
                  <Checkbox
                    size="small"
                    sx={{ p: 0.25, mr: 1 }}
                    checked={checklist[item.key] === true}
                    onChange={(e) =>
                      setChecklist((c) => ({
                        ...c,
                        [item.key]: e.target.checked,
                      }))
                    }
                    inputProps={{
                      "data-testid": "hub-checklist-item",
                      "data-key": item.key,
                    }}
                  />
                }
                label={<Typography typography="s1">{item.text}</Typography>}
              />
            ))}
          </Box>
        )}
      </ReviewSection>
    </>
  );

  const rulesChangedAlert = (
    <Alert
      severity="warning"
      data-testid="hub-rules-changed"
      action={
        <Button
          color="inherit"
          size="small"
          onClick={() => {
            decide.reset();
            setConfirming(false);
            setChecklist({});
            reloadRules();
          }}
        >
          Reload
        </Button>
      }
    >
      Rules changed, reload. The Hub rules changed while you reviewed; the
      checklist must be the current one.
    </Alert>
  );

  const card = (
    <Box
      component="section"
      aria-labelledby={headingId}
      data-testid="hub-decision-form"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      <Typography
        component="h2"
        id={headingId}
        typography="m3"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        Decision
      </Typography>
      {rulesChanged ? (
        rulesChangedAlert
      ) : (
        <>
          <Box
            component="ul"
            aria-label="Progress"
            data-testid="hub-decision-progress"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              display: "grid",
              gap: 0.75,
            }}
          >
            {progress.map((item) => (
              <Box
                component="li"
                key={item.key}
                data-state={item.state}
                sx={{ display: "flex", alignItems: "center", gap: 1 }}
              >
                <ProgressMark state={item.state} />
                <Typography
                  typography="s1"
                  color={
                    item.state === "todo" ? "text.secondary" : "text.primary"
                  }
                >
                  {item.text}
                </Typography>
              </Box>
            ))}
          </Box>
          <RadioGroup
            aria-labelledby={headingId}
            value={decision}
            onChange={(event) => choose(event.target.value)}
            sx={{
              gap: 0.5,
              pt: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {MODES.map((mode) => (
              <Box key={mode.value}>
                <FormControlLabel
                  value={mode.value}
                  sx={{ m: 0 }}
                  control={
                    <Radio
                      size="small"
                      sx={{ p: 0.5, mr: 0.5 }}
                      inputProps={{
                        "data-testid": mode.testId,
                        "aria-describedby": `${modeId}-${mode.value}`,
                      }}
                    />
                  }
                  label={
                    <Typography typography="s1" fontWeight={500}>
                      {mode.label}
                    </Typography>
                  }
                />
                <Typography
                  id={`${modeId}-${mode.value}`}
                  typography="s2"
                  color="text.secondary"
                  sx={{ pl: 4 }}
                >
                  {modeHint[mode.value]}
                </Typography>
              </Box>
            ))}
          </RadioGroup>
          {decision !== "approve" && (
            <TextField
              label={
                decision === "reject"
                  ? "Why it is not accepted (the submitter sees this)"
                  : "What to change (the submitter sees this)"
              }
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              multiline
              minRows={4}
              inputRef={reasonInput}
              inputProps={{
                maxLength: REASON_MAX,
                "data-testid": "hub-decision-reason-input",
              }}
              helperText={
                problem
                  ? `Write ${problem}.`
                  : `${reason.trim().length} / ${REASON_MAX}`
              }
              error={Boolean(reason) && Boolean(problem)}
            />
          )}
          {decision === "request_changes" && (
            <>
              <Typography typography="s1" fontWeight={500}>
                Notes on files (optional)
              </Typography>
              <ChangeNotesEditor
                notes={notes}
                onChange={setNotes}
                files={submission.files}
              />
            </>
          )}
          {rulesUnavailable && (
            <Box
              ref={rulesStatus}
              tabIndex={-1}
              role={rulesFetching ? "status" : undefined}
            >
              {rulesFetching ? (
                <Typography typography="s2" color="text.secondary">
                  Loading the review rules…
                </Typography>
              ) : rulesError ? (
                <Alert
                  severity="error"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={(event) => {
                        if (document.activeElement === event.currentTarget)
                          rulesStatus.current?.focus();
                        reloadRules();
                      }}
                    >
                      Try again
                    </Button>
                  }
                >
                  The review rules did not load. Try again before making a
                  decision.
                </Alert>
              ) : null}
            </Box>
          )}
          {decision === "approve" && (approveDisabled || diffError) && (
            <Box
              ref={diffStatus}
              tabIndex={-1}
              role={diffBusy ? "status" : undefined}
            >
              {diffBusy ? (
                <Typography typography="s2" color="text.secondary">
                  The changes are still loading.
                </Typography>
              ) : (
                <Alert
                  severity="warning"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={(event) => {
                        if (document.activeElement === event.currentTarget)
                          diffStatus.current?.focus();
                        onRetryDiff?.();
                      }}
                    >
                      Try again
                    </Button>
                  }
                >
                  {"The changes didn't load. Try again before approving."}
                </Alert>
              )}
            </Box>
          )}
          {!confirming && decisionError}
          <ConfirmDialog
            open={confirming}
            title={`Reject ${ref}?`}
            onClose={() => !decide.isPending && setConfirming(false)}
            closeDisabled={decide.isPending}
            content={
              <>
                <Typography typography="s2">
                  This submission cannot be reopened. The submitter receives
                  your reason by email.
                </Typography>
                {decisionError}
              </>
            }
            action={
              <Button
                variant="contained"
                color="error"
                disabled={disabled}
                onClick={submit}
              >
                Reject
              </Button>
            }
          />
          {decision === "approve" && blockers.length > 0 && (
            <Typography
              typography="s2"
              color="text.secondary"
              id="hub-approve-blockers"
              data-testid="hub-approve-blockers"
            >
              {`To approve: ${blockers.join("; ")}.`}
            </Typography>
          )}
          {decision !== "approve" && rulesUnavailable && (
            <Typography
              typography="s2"
              color="text.secondary"
              id="hub-decision-rules-blocker"
              data-testid="hub-decision-rules-blocker"
            >
              {decision === "reject"
                ? "The review rules must load before rejecting."
                : "The review rules must load before requesting changes."}
            </Typography>
          )}
          <Button
            fullWidth
            variant={decision === "reject" ? "outlined" : "contained"}
            color={decision === "reject" ? "inherit" : "primary"}
            disabled={disabled}
            onClick={() => {
              if (decision === "reject") {
                decide.reset();
                setConfirming(true);
              } else submit();
            }}
            aria-describedby={
              decision === "approve" && blockers.length > 0
                ? "hub-approve-blockers"
                : decision !== "approve" && rulesUnavailable
                  ? "hub-decision-rules-blocker"
                  : undefined
            }
            data-testid="hub-decision-submit"
          >
            {decision === "approve"
              ? "Approve and publish"
              : decision === "reject"
                ? "Reject…"
                : "Request changes"}
          </Button>
        </>
      )}
      <Box aria-live="polite" sx={VISUALLY_HIDDEN}>
        {announcement}
      </Box>
    </Box>
  );

  const approving = decision === "approve" && !anyNo;
  const barText = barLine({
    anyNo,
    decision,
    ready: !disabled,
    groups: groups.length,
    answered,
    checked,
    items: checklistItems.length,
    blockers,
  });
  const bar =
    narrow && !rulesChanged ? (
      <Box
        role="region"
        aria-label="Decision shortcut"
        data-testid="hub-decision-bar"
        sx={{
          position: "sticky",
          bottom: 0,
          zIndex: 2,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1.5,
          px: 2,
          py: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
        }}
      >
        <Typography
          id={barTextId}
          typography="s1"
          color="text.primary"
          sx={{ minWidth: 0 }}
        >
          {barText}
        </Typography>
        {/* Only Approve acts from the bar; a request or a rejection is sent
            from the Decision card, where its reason is written. */}
        <Button
          size="small"
          variant="contained"
          disabled={approving ? disabled : false}
          aria-describedby={barTextId}
          onClick={approving ? submit : goToReason}
          sx={{ flexShrink: 0 }}
        >
          {approving
            ? "Approve and publish"
            : decision === "reject"
              ? "Go to the reason"
              : "Go to the request"}
        </Button>
      </Box>
    ) : null;

  return layout({ main, side: card, bar });
}

DecisionForm.propTypes = {
  submission: PropTypes.object.isRequired,
  onDecided: PropTypes.func,
  approveDisabled: PropTypes.bool,
  diffError: PropTypes.object,
  diffLoading: PropTypes.bool,
  onRetryDiff: PropTypes.func,
  viewer: PropTypes.object,
  onOpenFile: PropTypes.func,
  layout: PropTypes.func,
};
