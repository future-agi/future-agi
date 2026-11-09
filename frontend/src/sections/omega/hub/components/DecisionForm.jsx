import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import { ConfirmDialog } from "src/components/custom-dialog";

import { REASON_MAX } from "../constants";
import { useDecision, useHubRules } from "../api/queries";
import {
  approveBlockers,
  decisionPayload,
  reasonProblem,
} from "../lib/decision";
import { DISCLOSURE_FIELDS } from "../lib/disclosure";
import { allFindings } from "../lib/report";

import ChangeNotesEditor from "./ChangeNotesEditor";
import DisclosureEditor from "./DisclosureEditor";
import { HubSection } from "./HubStates";

function initialDisclosure(draft) {
  return Object.fromEntries(
    DISCLOSURE_FIELDS.map((field) => [field, draft?.[field] || ""]),
  );
}

// The decision (SPEC §5.3, §5.4). The checklist comes from GET /rules; the
// form holds no rule text of its own. Approve stays disabled until every
// flag, every checklist item and every observed disclosure part is done.
export default function DecisionForm({
  submission,
  onDecided,
  approveDisabled = false,
  diffError,
  diffLoading = false,
  onRetryDiff,
}) {
  const {
    data: rules,
    refetch: reloadRules,
    isError: rulesError,
    isFetching: rulesFetching,
  } = useHubRules();
  const rulesStatus = useRef(null);
  const diffStatus = useRef(null);
  const rulesUnavailable = !rules || rulesError;
  const diffBusy =
    (approveDisabled || diffError) && (diffLoading || !diffError);
  const decide = useDecision(submission.id);
  const [decision, setDecision] = useState("approve");
  const [confirming, setConfirming] = useState(false);
  const [acknowledged, setAcknowledged] = useState([]);
  const [checklist, setChecklist] = useState({});
  const [disclosure, setDisclosure] = useState(() =>
    initialDisclosure(submission.draftDisclosure),
  );
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState([]);
  const flags = submission.flags || [];
  const findings = allFindings(submission.report).filter(
    (f) => f.severity === "flag",
  );
  const checklistItems = rules?.checklist || [];
  const rulesChanged = decide.error?.code === "OMEGA_HUB_RULES_CHANGED";

  const blockers = approveBlockers({
    flags,
    acknowledged,
    checklistItems,
    checklist,
    disclosure,
    observed: submission.observed,
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

  const toggleFlag = (id) =>
    setAcknowledged((list) =>
      list.includes(id) ? list.filter((x) => x !== id) : [...list, id],
    );
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

  if (rulesChanged) {
    return (
      <HubSection title="Decision" testId="hub-decision-form">
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
      </HubSection>
    );
  }

  return (
    <HubSection title="Decision" testId="hub-decision-form">
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
              The review rules did not load. Try again before making a decision.
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
      <ToggleButtonGroup
        exclusive
        size="small"
        fullWidth
        value={decision}
        onChange={(_, next) => next && setDecision(next)}
        aria-label="Decision"
      >
        <ToggleButton value="approve" data-testid="hub-decision-approve-tab">
          Approve
        </ToggleButton>
        <ToggleButton
          value="request_changes"
          data-testid="hub-decision-changes-tab"
        >
          Request changes
        </ToggleButton>
        <ToggleButton value="reject" data-testid="hub-decision-reject-tab">
          Reject
        </ToggleButton>
      </ToggleButtonGroup>

      {decision === "approve" ? (
        <>
          <Typography
            typography="s2"
            fontWeight={600}
          >{`Flagged calls (${flags.length})`}</Typography>
          {flags.length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              No flags to acknowledge.
            </Typography>
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column" }}>
              {flags.map((id) => {
                const finding = findings.find((f) => f.id === id);
                return (
                  <FormControlLabel
                    key={id}
                    control={
                      <Checkbox
                        size="small"
                        checked={acknowledged.includes(id)}
                        onChange={() => toggleFlag(id)}
                        inputProps={{
                          "data-testid": "hub-flag-ack",
                          "data-flag": id,
                        }}
                      />
                    }
                    label={
                      <Typography
                        typography="s2"
                        sx={{ overflowWrap: "anywhere" }}
                      >
                        {finding
                          ? `${finding.message}${finding.file ? ` (${finding.file}${finding.line ? `:${finding.line}` : ""})` : ""}`
                          : id}
                      </Typography>
                    }
                  />
                );
              })}
            </Box>
          )}
          <Typography typography="s2" fontWeight={600}>
            {rules ? `Checklist (rules version ${rules.version})` : "Checklist"}
          </Typography>
          <Box
            sx={{ display: "flex", flexDirection: "column" }}
            data-testid="hub-checklist"
          >
            {checklistItems.map((item) => (
              <FormControlLabel
                key={item.key}
                sx={{ alignItems: "flex-start", my: 0.25 }}
                control={
                  <Checkbox
                    size="small"
                    sx={{ pt: 0.25 }}
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
                label={<Typography typography="s2">{item.text}</Typography>}
              />
            ))}
          </Box>
          <Typography typography="s2" fontWeight={600}>
            Disclosure, as it will be published
          </Typography>
          <DisclosureEditor
            value={disclosure}
            onChange={setDisclosure}
            observed={submission.observed}
            draft={submission.draftDisclosure}
          />
        </>
      ) : (
        <>
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
          {decision === "request_changes" && (
            <>
              <Typography typography="s2" fontWeight={600}>
                Notes on files (optional)
              </Typography>
              <ChangeNotesEditor
                notes={notes}
                onChange={setNotes}
                files={submission.files}
              />
            </>
          )}
        </>
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
              This submission cannot be reopened. The submitter receives your
              reason by email.
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
          typography="s3"
          color="text.secondary"
          id="hub-approve-blockers"
          data-testid="hub-approve-blockers"
        >
          {`To approve: ${blockers.join("; ")}.`}
        </Typography>
      )}
      {decision !== "approve" && rulesUnavailable && (
        <Typography
          typography="s3"
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
            ? "Reject"
            : "Request changes"}
      </Button>
    </HubSection>
  );
}

DecisionForm.propTypes = {
  submission: PropTypes.object.isRequired,
  onDecided: PropTypes.func,
  approveDisabled: PropTypes.bool,
  diffError: PropTypes.object,
  diffLoading: PropTypes.bool,
  onRetryDiff: PropTypes.func,
};
