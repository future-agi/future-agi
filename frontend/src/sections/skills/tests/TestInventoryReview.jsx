import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import Section from "src/components/section";

const fileKey = (skillId, file) => `${skillId}:${file.path}:${file.sha256}`;

export default function TestInventoryReview({
  preview,
  value,
  onChange,
  disabled = false,
}) {
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
  return (
    <Stack spacing={2}>
      <Section
        title="Review every model-visible file"
        description="All listed files remain available. To remove answer keys, edit the real draft and preview again."
      >
        <Stack spacing={1.5}>
          {preview.inventory.map((bundle, index) => (
            <Box
              key={`${bundle.skillId}:${bundle.digest}:${index}`}
              sx={{ minWidth: 0 }}
            >
              <Typography
                variant="s3"
                color="text.secondary"
                sx={{ overflowWrap: "anywhere" }}
              >
                Skill {bundle.skillId} · {bundle.digest}
              </Typography>
              {bundle.files.map((file) => (
                <Box
                  key={file.path}
                  sx={{
                    py: 1,
                    borderBottom: 1,
                    borderColor: "divider",
                    overflowWrap: "anywhere",
                  }}
                >
                  <Typography variant="s1" sx={{ fontFamily: "monospace" }}>
                    {file.path}
                  </Typography>
                  <Typography variant="s3" color="text.secondary">
                    {file.sizeBytes} bytes · SHA-256 {file.sha256}
                  </Typography>
                  {file.flags.length > 0 && (
                    <Typography
                      variant="s3"
                      component="p"
                      color="text.secondary"
                    >
                      Flags:{" "}
                      {file.flags
                        .map((flag) => flag.replaceAll("_", " "))
                        .join(", ")}
                    </Typography>
                  )}
                </Box>
              ))}
            </Box>
          ))}
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
        label="I know of no bundled answer keys or evaluation material"
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
        label="I reviewed the entire inventory and outbound inputs"
      />
      <Typography variant="s3" color="text.secondary">
        Detection is not proof of independence. A diagnostic run keeps all files
        and cannot claim answer improvement.
      </Typography>
    </Stack>
  );
}
TestInventoryReview.propTypes = {
  preview: PropTypes.object.isRequired,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
