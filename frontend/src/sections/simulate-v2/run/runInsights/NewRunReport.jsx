import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SCOPE_LABEL,
  TEMPLATES,
  previewOf,
  reportCreditsOf,
  scopeIdsOf,
  templateOf,
} from "../../_mock/runInsightReports";
import { aboutCredits } from "../../_mock/trialQuestions";

/*
  New report — Observe's report builder for a run: pick a template (or ask
  your own question), which trials it reads, preview it on three, then run.
  Nothing is read or spent before Run report.
*/
const FieldLabel = ({ children }) => (
  <Typography
    sx={{ typography: "s1", fontWeight: 500, color: "text.primary", mb: 0.75 }}
  >
    {children}
  </Typography>
);
FieldLabel.propTypes = { children: PropTypes.node };

export default function NewRunReport({
  tasks,
  insights,
  runLabel,
  initial,
  onCancel,
  onRun,
}) {
  const [template, setTemplate] = useState(initial?.template || "failures");
  const [question, setQuestion] = useState(
    initial?.template === "custom" ? initial.title : "",
  );
  const [scope, setScope] = useState(
    initial?.scope || templateOf(initial?.template || "failures").scope,
  );
  const [preview, setPreview] = useState(null);
  const n = scopeIdsOf(scope, tasks).length;
  const failedN = scopeIdsOf("failed", tasks).length;
  const byId = new Map(tasks.map((t) => [t.id, t]));

  const pick = (id) => {
    setTemplate(id);
    setScope(templateOf(id).scope === "failed" && failedN ? "failed" : "all");
    setPreview(null);
  };

  return (
    <Box sx={{ maxWidth: 760 }}>
      <Typography sx={{ typography: "m1", fontWeight: 600 }}>
        New report
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.secondary", mt: 0.5 }}>
        Reads this run&apos;s saved trials and groups them, with the trials
        behind every finding. Your agent isn&apos;t run again.
      </Typography>

      <Stack spacing={2.5} sx={{ mt: 3 }}>
        <Box>
          <FieldLabel>What do you want to know?</FieldLabel>
          <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
            {TEMPLATES.map((t) => {
              const on = template === t.id;
              return (
                <ButtonBase
                  key={t.id}
                  onClick={() => pick(t.id)}
                  sx={{
                    width: "100%",
                    justifyContent: "flex-start",
                    textAlign: "left",
                    gap: 1.25,
                    px: 1,
                    minHeight: 48,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    bgcolor: on ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: on ? "action.selected" : "action.hover",
                    },
                  }}
                >
                  <Iconify
                    icon={t.icon}
                    width={16}
                    sx={{ color: "text.secondary", flexShrink: 0 }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      sx={{
                        typography: "s1",
                        fontWeight: 500,
                        color: "text.primary",
                      }}
                    >
                      {t.title}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {t.about}
                    </Typography>
                  </Box>
                  {on && (
                    <Iconify
                      icon="solar:check-linear"
                      width={16}
                      sx={{ color: "text.primary" }}
                    />
                  )}
                </ButtonBase>
              );
            })}
          </Box>
        </Box>

        {template === "custom" && (
          <Box>
            <FieldLabel>Your question</FieldLabel>
            <TextField
              fullWidth
              multiline
              minRows={2}
              size="small"
              placeholder="Why do refund requests fail when the caller is pushy?"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
          </Box>
        )}

        <Box>
          <FieldLabel>Which trials</FieldLabel>
          <TextField
            select
            size="small"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              setPreview(null);
            }}
            sx={{ minWidth: 280 }}
          >
            {Object.entries(SCOPE_LABEL).map(([v, label]) => (
              <MenuItem key={v} value={v} disabled={v === "failed" && !failedN}>
                {label} · {scopeIdsOf(v, tasks).length}
              </MenuItem>
            ))}
          </TextField>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            From {runLabel}. Every one is read — no sampling below 500 trials.
          </Typography>
        </Box>

        <Box>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Button
              size="small"
              variant="outlined"
              disabled={!n}
              onClick={() =>
                setPreview(previewOf({ template, scope, insights, tasks }))
              }
            >
              Preview on 3 trials
            </Button>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Free · see how trials will be grouped first
            </Typography>
          </Stack>
          {preview && (
            <Box sx={{ mt: 1, borderTop: "1px solid", borderColor: "divider" }}>
              {preview.length ? (
                preview.map((p) => {
                  const t = byId.get(p.id);
                  return (
                    <Stack
                      key={p.id}
                      direction="row"
                      spacing={1.5}
                      alignItems="center"
                      sx={{
                        minHeight: 36,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                        px: 1,
                      }}
                    >
                      <Typography
                        noWrap
                        sx={{
                          typography: "s1",
                          fontWeight: 500,
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        #{tasks.indexOf(t) + 1} · {t.persona?.name || t.id}
                      </Typography>
                      <Iconify
                        icon="solar:arrow-right-linear"
                        width={14}
                        sx={{ color: "text.tertiary" }}
                      />
                      <Typography
                        noWrap
                        sx={{
                          typography: "s1",
                          color: "text.secondary",
                          flex: 1,
                          minWidth: 0,
                        }}
                      >
                        {p.label}
                      </Typography>
                    </Stack>
                  );
                })
              ) : (
                <Typography
                  sx={{ typography: "s1", color: "text.secondary", py: 1 }}
                >
                  These trials share no pattern to group. Try all trials.
                </Typography>
              )}
            </Box>
          )}
        </Box>

        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ pt: 1 }}>
          <Button
            variant="contained"
            color="primary"
            disabled={!n || (template === "custom" && !question.trim())}
            onClick={() => onRun({ template, question, scope })}
          >
            Run report
          </Button>
          {onCancel && (
            <Button onClick={onCancel} sx={{ color: "text.secondary" }}>
              Cancel
            </Button>
          )}
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {n} trials · {aboutCredits(reportCreditsOf(n))}
          </Typography>
        </Stack>
      </Stack>
    </Box>
  );
}
NewRunReport.propTypes = {
  tasks: PropTypes.array.isRequired,
  insights: PropTypes.object.isRequired,
  runLabel: PropTypes.string.isRequired,
  initial: PropTypes.object,
  onCancel: PropTypes.func,
  onRun: PropTypes.func.isRequired,
};
