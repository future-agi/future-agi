import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField,
  Typography,
} from "@mui/material";

import Section from "src/components/section";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import TechnicalDetails from '../components/TechnicalDetails';
import IdentityChip from "../components/IdentityChip";
import Acknowledgment from "../components/Acknowledgment";
import ActionReason from "../components/ActionReason";
import InventoryFileDrawer from "./InventoryFileDrawer";

const fileKey = (skillId, file) => `${skillId}:${file.path}:${file.sha256}`;
// The server accepts exactly these five fields (v11/inventory.py), so nothing else is added here.
const freshReview = (fingerprint) => ({
  fingerprint,
  reviewedAllFiles: false,
  noKnownAnswerKeys: false,
  classifications: [],
  falsePositiveSecretFindings: [],
});

const PROVIDERS = { openai: "OpenAI", anthropic: "Anthropic", google: "Google", azure: "Azure", bedrock: "Amazon Bedrock", mistral: "Mistral" };
/** A provider id as people read it ("openai" → "OpenAI"). */
// eslint-disable-next-line react-refresh/only-export-components -- shared by both Tests tabs.
export const providerName = (value) =>
  PROVIDERS[String(value || "").toLowerCase()] ||
  (value ? String(value).charAt(0).toUpperCase() + String(value).slice(1) : "Not reported");
/** An upper-bound estimate rounded up to the cent: "$0.24". */
// eslint-disable-next-line react-refresh/only-export-components -- shared by both Tests tabs.
export const centsUp = (usd) => `$${(Math.ceil(Number(usd) * 100) / 100).toFixed(2)}`;

/**
 * The one tick that sends a test (UXF-03): "Send these 3 files and the request to OpenAI,
 * up to $0.24. They contain no answer keys or grading material." The answer-key clause is
 * part of the sentence only when the tick attests it (`attestsNoAnswerKeys`), so the tick
 * never sets a field its words don't say.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the sentence is shared by both Tests tabs.
export function sendSentence({ fileCount, what, provider, upTo, attestsNoAnswerKeys = true }) {
  const files = fileCount === 1 ? "this file" : `these ${fileCount} files`;
  const cost = upTo ? `, up to ${upTo}` : "";
  const clause = attestsNoAnswerKeys
    ? ` ${fileCount === 1 ? "It contains" : "They contain"} no answer keys or grading material.`
    : "";
  return `Send ${files} and ${what} to ${provider}${cost}.${clause}`;
}

export default function TestInventoryReview({
  preview,
  value,
  onChange,
  disabled = false,
  subjectKind,
  subjectId,
  subjectName,
  consentHeading,
  children,
  sendLabel,
  attestsNoAnswerKeys = true,
  consentDisabled = false,
  consentReason = null,
}) {
  // With `sendLabel`, the two file statements and the caller's provider consent are one tick
  // (UXF-03, D16). It counts only for the preview it was given for: a new fingerprint unticks it.
  const merged = Boolean(sendLabel);
  // Keep the complete inventory one click away; required findings and unticked
  // acknowledgments stay inline at the decision, never inside the disclosure.
  const [inspected, setInspected] = useState(null);
  const files = preview.inventory.flatMap((bundle) => bundle.files);
  const bytes = files.reduce((total, file) => total + file.sizeBytes, 0);
  const flaggedCount = files.filter((file) => file.flags.length > 0).length;
  const flagged = preview.inventory.flatMap((bundle) =>
    bundle.files
      .filter(
        (file) =>
          file.flags.includes("evaluation_artifact") ||
          file.flags.includes("case_overlap"),
      )
      .map((file) => ({ skillId: bundle.skillId, ...file })),
  );
  const uniqueFlagged = [
    ...new Map(
      flagged.map((file) => [fileKey(file.skillId, file), file]),
    ).values(),
  ];
  const update = (patch) => {
    const review = { ...value, ...patch, fingerprint: preview.fingerprint };
    const classified = uniqueFlagged.every((file) =>
      review.classifications.some(
        (item) =>
          fileKey(item.skillId, item) === fileKey(file.skillId, file) &&
          item.reason.trim(),
      ),
    );
    const secretsReviewed = preview.outboundFindings.every((finding) =>
      review.falsePositiveSecretFindings.some(
        (item) => item.findingId === finding.findingId && item.reason.trim(),
      ),
    );
    onChange({
      review,
      complete: review.reviewedAllFiles && classified && secretsReviewed,
      diagnostic:
        !review.noKnownAnswerKeys ||
        flagged.some((file) => file.flags.includes("case_overlap")) ||
        review.classifications.some(
          (item) => item.classification === "evaluation_material",
        ),
    });
  };
  const classify = (file, patch) => {
    const current = value.classifications.find(
      (item) => fileKey(item.skillId, item) === fileKey(file.skillId, file),
    );
    update({
      classifications: [
        ...value.classifications.filter(
          (item) => fileKey(item.skillId, item) !== fileKey(file.skillId, file),
        ),
        {
          skillId: file.skillId,
          path: file.path,
          sha256: file.sha256,
          classification: "reference_material",
          reason: "",
          ...current,
          ...patch,
        },
      ],
    });
  };
  const [bulk, setBulk] = useState({ classification: "", reason: "" });
  const staleReview =
    value.fingerprint !== preview.fingerprint &&
    (value.reviewedAllFiles ||
      value.noKnownAnswerKeys ||
      value.classifications.length > 0 ||
      value.falsePositiveSecretFindings.length > 0);
  useEffect(() => {
    // A review given for an earlier preview never carries over: the caller is told it is empty.
    if (staleReview)
      onChange({ review: freshReview(preview.fingerprint), complete: false, diagnostic: true });
  }, [staleReview, preview.fingerprint, onChange]);
  const sent =
    value.fingerprint === preview.fingerprint && value.reviewedAllFiles;
  const sendAcknowledgment = {
    identity: preview.fingerprint,
    acknowledged: sent,
    changed: false,
    setAcknowledged: (checked) => {
      if (disabled || consentDisabled) return;
      update({
        reviewedAllFiles: Boolean(checked),
        noKnownAnswerKeys: Boolean(checked) && attestsNoAnswerKeys,
      });
    },
  };
  const classifyAll = () => {
    const keys = new Set(uniqueFlagged.map((file) => fileKey(file.skillId, file)));
    update({
      classifications: [
        ...value.classifications.filter(
          (item) => !keys.has(fileKey(item.skillId, item)),
        ),
        ...uniqueFlagged.map((file) => ({
          skillId: file.skillId,
          path: file.path,
          sha256: file.sha256,
          classification: bulk.classification,
          reason: bulk.reason.trim(),
        })),
      ],
    });
  };
  const snapshot = (bundle) => [preview.candidate, preview.production, preview.reference, ...(preview.competitors || [])].find((item) => item?.digest === bundle.digest && (item.skillId ?? item.skill_id) === bundle.skillId);
  const sourceLabel = (bundle) => {
    const source = snapshot(bundle);
    const name = bundle.skillId === subjectId ? subjectName || (subjectKind === 'agent_context' ? 'This context' : 'This skill') : bundle.name || 'Linked skill';
    return `${name}${source?.version ? ` v${source.version}` : source?.draftRevision ? ` · draft revision ${source.draftRevision}` : ''}`;
  };
  return (
    <Stack spacing={2}>
      <Section
        title="Review every file sent to the model"
        description="All listed files remain available. To remove answer keys, edit the real draft and preview again."
      >
        <Stack spacing={1.5}>
          <Typography variant="s1">{files.length} files · {flaggedCount} flagged · {bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(1)} KB`}</Typography>
          <TechnicalDetails title="View all files sent to the model" defaultOpen>
          <TableContainer><Table size="small" aria-label="Model-visible inventory">
            <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>File</TableCell><TableCell>Source/version</TableCell><TableCell>Flags</TableCell><TableCell>Size</TableCell><TableCell /></TableRow></TableHead>
            <TableBody>{preview.inventory.flatMap((bundle, index) => bundle.files.map((file) => <TableRow key={`${index}:${file.path}`} hover sx={{ height: ROW_HEIGHT.default }}>
              <TableCell sx={{ overflowWrap: 'anywhere' }}><Typography variant="s1">{file.path}</Typography></TableCell>
              <TableCell><Typography variant="s1">{sourceLabel(bundle)}</Typography><TechnicalDetails title="Source details"><IdentityChip value={bundle.skillId} label="Source" kind="id" /><IdentityChip value={bundle.digest} label="Version" /></TechnicalDetails></TableCell>
              <TableCell><Typography variant="s3">{file.flags.length ? file.flags.map((flag) => flag.replaceAll('_', ' ')).join(', ') : 'None'}</Typography></TableCell>
              <TableCell><Typography variant="s3">{file.sizeBytes} bytes</Typography></TableCell>
              <TableCell><Button size="small" variant="text" color="inherit" aria-label={`View ${file.path}`} onClick={() => setInspected({ bundle, file, fingerprint: preview.fingerprint })}>View</Button></TableCell>
            </TableRow>))}</TableBody>
          </Table></TableContainer>
          </TechnicalDetails>
          {inspected && inspected.fingerprint === preview.fingerprint && <InventoryFileDrawer key={`${preview.fingerprint}:${inspected.file.path}:${inspected.file.sha256}`} subjectKind={subjectKind} subjectId={subjectId} candidate={[preview.candidate, preview.production, preview.reference, ...(preview.competitors || [])].find((item) => item?.digest === inspected.bundle.digest && (item.skillId ?? item.skill_id) === inspected.bundle.skillId)} bundle={inspected.bundle} file={inspected.file} onClose={() => setInspected(null)} />}
          {uniqueFlagged.length > 1 && (
            <Stack spacing={1} data-testid="bulk-classification">
              <Typography variant="s1">
                {uniqueFlagged.length} files are flagged. Classify them together
                after reading the list above; each file keeps its own record and
                can still be changed below.
              </Typography>
              <TextField
                select
                size="small"
                disabled={disabled}
                label={`Classify all ${uniqueFlagged.length} flagged files`}
                value={bulk.classification}
                onChange={(event) =>
                  setBulk({ ...bulk, classification: event.target.value })
                }
              >
                <MenuItem value="reference_material">
                  Reference material
                </MenuItem>
                <MenuItem value="evaluation_material">
                  Evaluation material · diagnostic only
                </MenuItem>
              </TextField>
              <TextField
                size="small"
                disabled={disabled}
                label={`Reason for all ${uniqueFlagged.length} flagged files`}
                value={bulk.reason}
                inputProps={{ maxLength: 2000 }}
                onChange={(event) =>
                  setBulk({ ...bulk, reason: event.target.value })
                }
              />
              <Button
                variant="outlined"
                size="small"
                disabled={
                  disabled || !bulk.classification || !bulk.reason.trim()
                }
                onClick={classifyAll}
              >
                {`Apply to all ${uniqueFlagged.length} flagged files`}
              </Button>
            </Stack>
          )}
          {uniqueFlagged.map((file) => {
            const classification = value.classifications.find(
              (item) =>
                fileKey(item.skillId, item) === fileKey(file.skillId, file),
            );
            return (
              <Stack key={fileKey(file.skillId, file)} spacing={1}>
                <TextField
                  select
                  size="small"
                  disabled={disabled}
                  label={`Classify ${file.path} (${file.sha256.slice(0, 8)})`}
                  value={classification?.classification ?? ""}
                  onChange={(event) =>
                    classify(file, { classification: event.target.value })
                  }
                >
                  <MenuItem value="reference_material">
                    Reference material
                  </MenuItem>
                  <MenuItem value="evaluation_material">
                    Evaluation material · diagnostic only
                  </MenuItem>
                </TextField>
                <TextField
                  size="small"
                  disabled={disabled}
                  label={`Classification reason for ${file.path} (${file.sha256.slice(0, 8)})`}
                  value={classification?.reason ?? ""}
                  inputProps={{ maxLength: 2000 }}
                  onChange={(event) =>
                    classify(file, { reason: event.target.value })
                  }
                />
                {file.flags.includes("case_overlap") && (
                  <Alert severity="warning">
                    Case or expected-answer overlap makes this diagnostic even
                    when classified as reference material.
                  </Alert>
                )}
              </Stack>
            );
          })}
        </Stack>
      </Section>
      {preview.outboundFindings.length > 0 && (
        <Section
          title="Review outbound secret findings"
          description="Remove real secrets from files, requests, fixtures and checks. Only acknowledge genuine false positives."
        >
          <Stack spacing={1.5}>
            {preview.outboundFindings.map((finding) => {
              const accepted = value.falsePositiveSecretFindings.find(
                (item) => item.findingId === finding.findingId,
              );
              return (
                <Stack key={finding.findingId} spacing={1}>
                  <Typography variant="s1">
                    {finding.code} · {finding.explanation}
                  </Typography>
                  <Typography
                    variant="s3"
                    color="text.secondary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {JSON.stringify(finding.location)}
                  </Typography>
                  <FormControlLabel
                    control={
                      <Checkbox
                        disabled={disabled}
                        checked={Boolean(accepted)}
                        onChange={(event) =>
                          update({
                            falsePositiveSecretFindings: event.target.checked
                              ? [
                                  ...value.falsePositiveSecretFindings,
                                  { findingId: finding.findingId, reason: "" },
                                ]
                              : value.falsePositiveSecretFindings.filter(
                                  (item) =>
                                    item.findingId !== finding.findingId,
                                ),
                          })
                        }
                      />
                    }
                    label="This is not a real secret"
                  />
                  {accepted && (
                    <TextField
                      size="small"
                      disabled={disabled}
                      label={`False-positive reason (${finding.findingId})`}
                      value={accepted.reason}
                      inputProps={{ maxLength: 2000 }}
                      onChange={(event) =>
                        update({
                          falsePositiveSecretFindings:
                            value.falsePositiveSecretFindings.map((item) =>
                              item.findingId === finding.findingId
                                ? { ...item, reason: event.target.value }
                                : item,
                            ),
                        })
                      }
                    />
                  )}
                </Stack>
              );
            })}
          </Stack>
        </Section>
      )}
      {merged ? (
        <Stack spacing={0.5} {...(consentHeading ? { role: "group", "aria-labelledby": "test-consents-heading" } : {})}>
          {consentHeading && <Typography id="test-consents-heading" component="h3" variant="m3">{consentHeading}</Typography>}
          <Acknowledgment
            acknowledgment={sendAcknowledgment}
            label={sendLabel}
            disabled={disabled || consentDisabled}
          />
          <ActionReason id="test-send-reason" reason={consentReason} />
        </Stack>
      ) : (
        // Legacy layout for callers that keep separate statements: unticked and in this order.
        <Stack spacing={consentHeading ? 0.5 : 2} {...(consentHeading ? { role: "group", "aria-labelledby": "test-consents-heading" } : {})}>
          {consentHeading && <Typography id="test-consents-heading" component="h3" variant="m3">{consentHeading}</Typography>}
          <Typography variant="s3" color="text.secondary">Read the files above and check what will be sent to the model before confirming.</Typography>
          <FormControlLabel
            control={
              <Checkbox
                disabled={disabled}
                checked={value.noKnownAnswerKeys}
                onChange={(event) =>
                  update({ noKnownAnswerKeys: event.target.checked })
                }
              />
            }
            label="The files sent to the model contain no answer keys or grading material."
          />
          <FormControlLabel
            control={
              <Checkbox
                disabled={disabled}
                checked={value.reviewedAllFiles}
                onChange={(event) =>
                  update({ reviewedAllFiles: event.target.checked })
                }
              />
            }
            label="I reviewed every file and input that will be sent to the model."
          />
          {children}
        </Stack>
      )}
      <TechnicalDetails title="About this preview"><Typography variant="s3" color="text.secondary">
        Detection is not proof of independence. A diagnostic run keeps all files
        and cannot claim answer improvement.
      </Typography></TechnicalDetails>
    </Stack>
  );
}
TestInventoryReview.propTypes = {
  preview: PropTypes.object.isRequired,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  subjectKind: PropTypes.string,
  subjectId: PropTypes.string,
  subjectName: PropTypes.string,
  /** Opt-in heading that groups the acknowledgments (and any `children` consents) at the decision. */
  consentHeading: PropTypes.string,
  children: PropTypes.node,
  /** The one send tick (see `sendSentence`). Without it the two file statements stay separate. */
  sendLabel: PropTypes.string,
  /** What the send tick sets for "no known answer keys"; its label must say the same. */
  attestsNoAnswerKeys: PropTypes.bool,
  /** Holds the send tick (for example while paid runs are off), with the reason beside it. */
  consentDisabled: PropTypes.bool,
  consentReason: PropTypes.node,
};
