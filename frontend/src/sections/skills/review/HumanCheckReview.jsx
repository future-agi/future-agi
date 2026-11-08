import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { IdentityValue, ReviewState } from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

function AnnotationForm({
  reference,
  machineVerdict,
  annotations,
  canAnnotate,
  onAppend,
  onRefresh,
}) {
  const [verdict, setVerdict] = useState("");
  const [citedText, setCitedText] = useState("");
  const [comment, setComment] = useState("");
  const [revision, setRevision] = useState(reference.annotationRevision);
  const operation = useReviewedOperation();
  const stale = revision !== reference.annotationRevision;
  const locked =
    operation.busy || operation.uncertain || Boolean(operation.result);
  return (
    <Section title="Human review">
      <Stack spacing={1.5}>
        <Stack direction="row" spacing={1}>
          <Typography variant="s1">Machine verdict</Typography>
          <StatusBadge status={machineVerdict} />
        </Stack>
        <Typography variant="s3">
          Append a cited review without changing the machine grade. Any new
          annotation advances evidence revision and requires a new release
          review.
        </Typography>
        <IdentityValue
          label="Check / attempt"
          value={`${reference.checkId} / ${reference.attemptId}`}
        />
        {annotations.map((item) => (
          <Stack key={item.id ?? item.revision} spacing={0.5}>
            <Typography variant="m3">
              {item.verdict === "cannot_assess"
                ? "Cannot assess"
                : item.verdict === "agree"
                  ? "Agree"
                  : "Disagree"}{" "}
              · {item.actor?.name ?? item.actorId ?? "Attributed reviewer"} · r
              {item.revision}
            </Typography>
            <Typography variant="s3">{item.createdAt}</Typography>
            <Typography
              variant="s1"
              component="blockquote"
              sx={{
                m: 0,
                pl: 1.5,
                borderLeft: 1,
                borderColor: "divider",
                overflowWrap: "anywhere",
                whiteSpace: "pre-wrap",
              }}
            >
              {item.citedText}
            </Typography>
            <Typography
              variant="s1"
              sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
            >
              {item.comment}
            </Typography>
          </Stack>
        ))}
        {!annotations.length && (
          <Typography variant="s3">No human annotations loaded.</Typography>
        )}
        <TextField
          select
          label="Your assessment"
          size="small"
          value={verdict}
          onChange={(event) => setVerdict(event.target.value)}
          disabled={!canAnnotate || locked}
        >
          <MenuItem value="">Choose an assessment</MenuItem>
          <MenuItem value="agree">Agree</MenuItem>
          <MenuItem value="disagree">Disagree</MenuItem>
          <MenuItem value="cannot_assess">Cannot assess</MenuItem>
        </TextField>
        <TextField
          label="Cited clause or answer excerpt"
          size="small"
          multiline
          minRows={2}
          value={citedText}
          inputProps={{ maxLength: 2000 }}
          onChange={(event) => setCitedText(event.target.value)}
          disabled={!canAnnotate || locked}
          helperText="Cite only the relevant text. The server checks this against the retained check or attempt."
        />
        <TextField
          label="Review explanation"
          size="small"
          multiline
          minRows={2}
          value={comment}
          inputProps={{ maxLength: 2000 }}
          onChange={(event) => setComment(event.target.value)}
          disabled={!canAnnotate || locked}
        />
        {stale && (
          <Alert severity="warning">
            Another annotation arrived. Your explanation is preserved. Inspect
            it before acknowledging revision {reference.annotationRevision}.
          </Alert>
        )}
        {stale && (
          <Button
            disabled={locked}
            onClick={() => setRevision(reference.annotationRevision)}
          >
            I reviewed the new annotations
          </Button>
        )}
        <OperationFeedback operation={operation} />
        {operation.result && (
          <Alert severity="info">
            Annotation recorded. Machine grade unchanged. Refresh the release
            review before deciding.
          </Alert>
        )}
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            disabled={
              !onAppend ||
              !canAnnotate ||
              locked ||
              stale ||
              !verdict ||
              !comment.trim() ||
              (verdict !== "cannot_assess" && !citedText.trim())
            }
            onClick={() =>
              operation.run(onAppend, {
                ...reference,
                annotationRevision: revision,
                verdict,
                citedText,
                comment,
              })
            }
          >
            Append review
          </Button>
          {onRefresh && (
            <Button disabled={operation.busy} onClick={onRefresh}>
              Refresh annotations
            </Button>
          )}
        </Stack>
      </Stack>
    </Section>
  );
}
AnnotationForm.propTypes = {
  reference: PropTypes.object.isRequired,
  machineVerdict: PropTypes.string.isRequired,
  annotations: PropTypes.array.isRequired,
  canAnnotate: PropTypes.bool,
  onAppend: PropTypes.func,
  onRefresh: PropTypes.func,
};

export default function HumanCheckReview({
  scopeKey,
  state = "unavailable",
  reference,
  annotations = [],
  ...props
}) {
  if (!scopeKey || state !== "ready" || !reference)
    return <ReviewState state={!scopeKey ? "forbidden" : state} />;
  return (
    <AnnotationForm
      key={`${scopeKey}:${reference.source}:${reference.sourceId}:${reference.attemptId}:${reference.checkId}`}
      {...props}
      {...{ reference, annotations }}
    />
  );
}
HumanCheckReview.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  reference: PropTypes.object,
  annotations: PropTypes.array,
};
