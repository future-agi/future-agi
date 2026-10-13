import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AgentDraftAssistant from "./AgentDraftAssistant";
import AgentModelField from "./AgentModelField";
import AgentSharingFields from "./AgentSharingFields";
import AgentSkillsFields from "./AgentSkillsFields";
import { agentSharingLabel } from "./agentSharing";
import { MAX_AGENT_EXAMPLES, examplesError } from "./agentExamples";

function AgentExamplesFields({ examples, onChange, readOnly }) {
  return (
    <Stack spacing={1.5}>
      {examples.map((example, index) => (
        <Box
          component="fieldset"
          aria-label={`Example ${index + 1}`}
          key={index}
          sx={{
            m: 0,
            minWidth: 0,
            p: 1.5,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 1 }}
          >
            <Typography variant="caption">Example {index + 1}</Typography>
            <Button
              size="small"
              color="error"
              aria-label={`Remove example ${index + 1}`}
              onClick={() =>
                onChange(examples.filter((_, candidate) => candidate !== index))
              }
              disabled={readOnly}
            >
              Remove
            </Button>
          </Stack>
          <Stack spacing={1}>
            <TextField
              fullWidth
              multiline
              minRows={2}
              label="Example prompt"
              value={example.prompt}
              disabled={readOnly}
              onChange={(event) =>
                onChange(
                  examples.map((candidate, candidateIndex) =>
                    candidateIndex === index
                      ? { ...candidate, prompt: event.target.value }
                      : candidate,
                  ),
                )
              }
            />
            <TextField
              fullWidth
              multiline
              minRows={2}
              label="Example response"
              value={example.response}
              disabled={readOnly}
              onChange={(event) =>
                onChange(
                  examples.map((candidate, candidateIndex) =>
                    candidateIndex === index
                      ? { ...candidate, response: event.target.value }
                      : candidate,
                  ),
                )
              }
            />
          </Stack>
        </Box>
      ))}
    </Stack>
  );
}
AgentExamplesFields.propTypes = {
  examples: PropTypes.arrayOf(
    PropTypes.shape({
      prompt: PropTypes.string.isRequired,
      response: PropTypes.string.isRequired,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
  readOnly: PropTypes.bool.isRequired,
};

const AGENT_STARTER_TEMPLATES = [
  {
    id: "blank",
    label: "Start blank",
    description: "Define the agent from scratch.",
    draft: {
      name: "",
      role: "",
      purpose: "",
      responsibilities: "",
      workMode: "on_demand",
    },
  },
  {
    id: "analyst",
    label: "Research analyst",
    description: "Turn a question into a concise, evidence-led brief.",
    draft: {
      name: "Research analyst",
      role: "Analyst",
      purpose:
        "Investigate a question and produce a concise brief grounded in the information available to the agent.",
      responsibilities:
        "Clarify the question and scope\nSeparate observations from assumptions\nWrite a concise findings brief",
      workMode: "on_demand",
    },
  },
  {
    id: "ticket-triage",
    label: "Ticket triage",
    description: "Organize incoming work and surface the next action.",
    draft: {
      name: "Ticket triage",
      role: "Triage specialist",
      purpose:
        "Review an incoming request, identify its urgency and owner, and explain the next action.",
      responsibilities:
        "Summarize the request\nIdentify urgency and missing context\nRecommend the next action and owner",
      workMode: "on_demand",
    },
  },
  {
    id: "report-assistant",
    label: "Report assistant",
    description: "Shape supplied facts into a readable report draft.",
    draft: {
      name: "Report assistant",
      role: "Reporting assistant",
      purpose:
        "Turn supplied facts and notes into a clear report draft with explicit unknowns.",
      responsibilities:
        "Organize the supplied facts\nCall out gaps and uncertainty\nDraft the report in a consistent structure",
      workMode: "on_demand",
    },
  },
];

// Scheduling is a separate tenant API. A starter creates only an on-demand definition.
export function AgentEditor({
  mode,
  draft,
  onChange,
  onCancel,
  onSubmit,
  busy,
  error,
  allowed = true,
  environmentSelection,
  environmentValid = true,
  modelSelection,
  modelValid = true,
  skillsSupported = false,
  skillsAllowed = false,
  skillSelection,
  skillsValid = true,
  sharingSupported = false,
  sharingCanManage = false,
  sharingDirectory,
  sharingValid = true,
  assistantClient,
}) {
  const isEdit = mode === "edit";
  const [step, setStep] = useState(isEdit || draft.importedDefinition ? 1 : 0);
  const [starter, setStarter] = useState("");
  const [replacement, setReplacement] = useState(null);
  const exampleError =
    !isEdit || draft.examplesChanged ? examplesError(draft.examples || []) : "";
  const versionKnown =
    !isEdit ||
    (Number.isSafeInteger(draft.expectedVersion) && draft.expectedVersion > 0);
  const valid =
    Boolean(draft.name.trim() && draft.role.trim() && draft.purpose.trim()) &&
    environmentValid &&
    modelValid &&
    skillsValid &&
    sharingValid &&
    !exampleError &&
    versionKnown;
  const environmentLabel = draft.environmentId
    ? `${environmentSelection?.selected?.name || draft.environmentId} · Revision ${draft.environmentRevision || "not selected"}`
    : "No environment attached";
  const applyTemplate = (template) => {
    onChange({
      ...template.draft,
      examples: [],
      sourceResponsibilities: undefined,
      importedDefinition: false,
    });
    setStarter(template.label);
    setReplacement(null);
    setStep(1);
  };
  const chooseTemplate = (template) => {
    if (
      [draft.name, draft.role, draft.purpose, draft.responsibilities].some(
        (value) => value.trim(),
      )
    )
      setReplacement(template);
    else applyTemplate(template);
  };
  const title = isEdit ? "Edit managed agent" : "Create a managed agent";
  return (
    <Card
      component="section"
      aria-label={title}
      variant="outlined"
      sx={{ mt: 2, borderColor: "primary.main" }}
    >
      <CardContent>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          gap={1}
          sx={{ mb: 0.75 }}
        >
          <Typography variant="h6">{title}</Typography>
          {!isEdit ? (
            <Chip size="small" variant="outlined" label="Draft" />
          ) : null}
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {isEdit
            ? "Update your agent’s goal and responsibilities."
            : "Give your agent a clear goal and a useful starting point."}
        </Typography>
        {draft.importedDefinition ? (
          <Alert severity="info" sx={{ mb: 2 }}>
            Review this new definition. Select its environment and authorize its
            tools in this workspace before running it.
          </Alert>
        ) : null}
        {!isEdit ? (
          <Stack
            component="ol"
            aria-label="Agent creation steps"
            direction="row"
            spacing={1}
            sx={{ p: 0, m: 0, mb: 2, listStyle: "none" }}
          >
            {["Start", "Define", "Review"].map((label, index) => (
              <Box
                component="li"
                key={label}
                aria-current={step === index ? "step" : undefined}
              >
                <Chip
                  size="small"
                  color={step === index ? "primary" : "default"}
                  variant={step === index ? "filled" : "outlined"}
                  label={`${index + 1}. ${label}`}
                />
              </Box>
            ))}
          </Stack>
        ) : null}
        {error ? (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        ) : null}
        {!versionKnown ? (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Read the latest agent definition, then reopen Edit before saving.
          </Alert>
        ) : null}
        {!allowed ? (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Saving is unavailable for the current selection or permissions. Your
            draft is kept here.
          </Alert>
        ) : null}
        {!isEdit &&
        step < 2 &&
        typeof assistantClient?.draftWorkforceAgent === "function" ? (
          <Box sx={{ mb: 2 }}>
            <AgentDraftAssistant
              client={assistantClient}
              draft={draft}
              disabled={busy || !allowed}
              onApply={(changes) => {
                onChange(changes);
                setStarter("Assistant");
                setReplacement(null);
                setStep(1);
              }}
            />
          </Box>
        ) : null}
        <Box
          component="form"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || !allowed || !valid) return;
            if (!isEdit && step === 1) setStep(2);
            else if (isEdit || step === 2) onSubmit();
          }}
        >
          {step === 0 ? (
            <Stack spacing={2}>
              <Box>
                <Typography variant="subtitle1" fontWeight={600}>
                  Choose a starting point
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Every starter is editable. Choose the one closest to the work
                  you have in mind.
                </Typography>
              </Box>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                  gap: 1.25,
                }}
              >
                {AGENT_STARTER_TEMPLATES.map((template) => (
                  <Button
                    key={template.id}
                    aria-label={template.label}
                    variant="outlined"
                    onClick={() => chooseTemplate(template)}
                    disabled={busy}
                    sx={{
                      p: 2,
                      minHeight: 112,
                      textAlign: "left",
                      justifyContent: "flex-start",
                      alignItems: "flex-start",
                      borderColor: "divider",
                      color: "text.primary",
                    }}
                  >
                    <Box>
                      <Typography
                        component="span"
                        variant="subtitle2"
                        sx={{ display: "block", mb: 0.5 }}
                      >
                        {template.label}
                      </Typography>
                      <Typography
                        component="span"
                        variant="body2"
                        color="text.secondary"
                      >
                        {template.description}
                      </Typography>
                    </Box>
                  </Button>
                ))}
              </Box>
              <Typography variant="caption" color="text.secondary">
                Starters provide draft instructions. Choose connections and
                schedules separately when they are available in your workspace.
              </Typography>
              {replacement ? (
                <Alert severity="warning">
                  Replace your current draft with{" "}
                  {replacement.label.toLowerCase()}?
                  <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                    <Button
                      size="small"
                      onClick={() => applyTemplate(replacement)}
                    >
                      Replace draft
                    </Button>
                    <Button
                      size="small"
                      onClick={() => {
                        setReplacement(null);
                        setStep(1);
                      }}
                    >
                      Keep my draft
                    </Button>
                  </Stack>
                </Alert>
              ) : null}
            </Stack>
          ) : step === 1 ? (
            <Stack spacing={2}>
              {!isEdit ? (
                <Box>
                  <Typography variant="subtitle1" fontWeight={600}>
                    What should this agent accomplish?
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {starter === "Start blank"
                      ? "Describe the outcome you want and the responsibilities it should take on."
                      : `Make this ${starter.toLowerCase()} draft your own.`}
                  </Typography>
                </Box>
              ) : null}
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
                <TextField
                  fullWidth
                  required
                  disabled={busy}
                  label="Name"
                  value={draft.name}
                  onChange={(event) => onChange({ name: event.target.value })}
                  placeholder="e.g. Customer insight analyst"
                />
                <TextField
                  fullWidth
                  required
                  disabled={busy}
                  label="Role"
                  value={draft.role}
                  onChange={(event) => onChange({ role: event.target.value })}
                  placeholder="e.g. Research analyst"
                />
              </Stack>
              <TextField
                fullWidth
                required
                disabled={busy}
                multiline
                minRows={3}
                label={isEdit ? "Purpose" : "Goal"}
                value={draft.purpose}
                onChange={(event) => onChange({ purpose: event.target.value })}
                helperText="Describe the outcome, who it helps, and what a useful result looks like."
                placeholder="Turn supplied customer feedback into a concise brief with recurring issues and next steps."
              />
              <TextField
                fullWidth
                disabled={busy}
                multiline
                minRows={4}
                label="Responsibilities"
                value={draft.responsibilities}
                onChange={(event) =>
                  onChange({ responsibilities: event.target.value })
                }
                helperText="One responsibility per line. Include boundaries or checks that matter to you."
              />
              <AgentModelField
                draft={draft}
                selection={modelSelection}
                onChange={onChange}
                disabled={busy || !allowed}
                isEdit={isEdit}
              />
              <AgentSharingFields
                sharing={draft.sharing}
                onChange={onChange}
                directory={sharingDirectory || { candidates: [] }}
                disabled={busy || !allowed}
                supported={sharingSupported}
                canManage={sharingCanManage}
              />
              {skillsSupported ? (
                <AgentSkillsFields
                  value={draft.skills}
                  onChange={(skills) => onChange({ skills })}
                  selection={skillSelection}
                  disabled={busy || !skillsAllowed}
                />
              ) : null}
              <Box component="section" aria-label="Agent examples">
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  sx={{ mb: 1 }}
                >
                  <Box>
                    <Typography variant="subtitle2">
                      Example responses
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Show the behavior you want. Each prompt and response is
                      limited to 2,000 UTF-8 bytes.
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    onClick={() =>
                      onChange({
                        examples: [
                          ...(draft.examples || []),
                          { prompt: "", response: "" },
                        ],
                      })
                    }
                    disabled={
                      busy ||
                      !allowed ||
                      draft.examples?.length >= MAX_AGENT_EXAMPLES ||
                      draft.examplesLoaded === false
                    }
                  >
                    Add example
                  </Button>
                </Stack>
                {draft.examplesLoaded === false ? (
                  <Alert severity="warning">
                    The complete examples could not be read. Refresh the agent
                    before replacing them.
                  </Alert>
                ) : null}
                <AgentExamplesFields
                  examples={draft.examples || []}
                  readOnly={busy || !allowed || draft.examplesLoaded === false}
                  onChange={(examples) => onChange({ examples })}
                />
                {exampleError ? (
                  <Alert severity="warning">{exampleError}</Alert>
                ) : null}
                {isEdit ? (
                  <Typography variant="caption" color="text.secondary">
                    Saving creates a definition revision. Pause and start the
                    agent to deploy the changes.
                  </Typography>
                ) : null}
              </Box>
              {isEdit || draft.importedDefinition ? (
                <TextField
                  select
                  fullWidth
                  disabled={busy}
                  label="Work mode"
                  value={draft.workMode}
                  onChange={(event) =>
                    onChange({ workMode: event.target.value })
                  }
                >
                  <MenuItem value="on_demand">On demand</MenuItem>
                  {draft.workMode !== "on_demand" ? (
                    <MenuItem value={draft.workMode}>
                      {draft.workMode === "always_on"
                        ? "Always on"
                        : draft.workMode.replaceAll("_", " ")}{" "}
                      (current)
                    </MenuItem>
                  ) : null}
                </TextField>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  <strong>On demand.</strong> Run this agent when you choose. A
                  starter adds no connections or schedule.
                </Typography>
              )}
              <Box>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  Execution environment
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 1.5 }}
                >
                  {environmentLabel}
                </Typography>
                {environmentSelection?.canSelect ? (
                  <Stack spacing={1.5}>
                    <TextField
                      select
                      fullWidth
                      label="Environment"
                      disabled={busy || !allowed}
                      value={draft.environmentId || ""}
                      onChange={(event) =>
                        onChange({
                          environmentId: event.target.value,
                          environmentRevision: "",
                        })
                      }
                      helperText="Optional. Choose an active environment and its exact revision."
                    >
                      <MenuItem value="">No environment attached</MenuItem>
                      {draft.environmentId && !environmentSelection.selected ? (
                        <MenuItem value={draft.environmentId} disabled>
                          {draft.environmentId} (unavailable)
                        </MenuItem>
                      ) : null}
                      {environmentSelection.environments.map((environment) => (
                        <MenuItem
                          key={environment.id}
                          value={environment.id}
                          disabled={environment.status !== "active"}
                        >
                          {environment.name} · {environment.id}
                          {environment.status !== "active"
                            ? ` (${environment.status})`
                            : ""}
                        </MenuItem>
                      ))}
                    </TextField>
                    {draft.environmentId ? (
                      <TextField
                        select
                        fullWidth
                        label="Environment revision"
                        value={draft.environmentRevision || ""}
                        disabled={
                          busy ||
                          !allowed ||
                          environmentSelection.loading ||
                          !environmentSelection.selected
                        }
                        onChange={(event) =>
                          onChange({
                            environmentRevision: Number(event.target.value),
                          })
                        }
                        helperText={
                          environmentSelection.loading
                            ? "Reading immutable revisions…"
                            : "The selected revision stays pinned when a newer revision is published."
                        }
                      >
                        <MenuItem value="" disabled>
                          Choose a revision
                        </MenuItem>
                        {draft.environmentRevision &&
                        !environmentSelection.exactRevision ? (
                          <MenuItem value={draft.environmentRevision} disabled>
                            Revision {draft.environmentRevision} (unverified)
                          </MenuItem>
                        ) : null}
                        {environmentSelection.revisions.map((revision) => (
                          <MenuItem
                            key={revision.revision}
                            value={revision.revision}
                          >
                            {`Revision ${revision.revision} · ${revision.name} · ${revision.pinnedImageDigest || revision.sandboxKind}`}
                          </MenuItem>
                        ))}
                      </TextField>
                    ) : null}
                    {environmentSelection.error ? (
                      <Alert
                        severity="warning"
                        action={
                          <Button
                            disabled={busy}
                            onClick={environmentSelection.reload}
                          >
                            Retry
                          </Button>
                        }
                      >
                        {environmentSelection.error}
                      </Alert>
                    ) : null}
                    {!environmentValid && !environmentSelection.loading ? (
                      <Alert severity="warning">
                        Choose an available environment revision or remove the
                        selection. Your draft is kept here.
                      </Alert>
                    ) : null}
                    <Typography variant="caption" color="text.secondary">
                      Omega checks runtime support when you save. Selecting a
                      revision does not start a worker.
                    </Typography>
                  </Stack>
                ) : (
                  <Typography variant="caption" color="text.secondary">
                    Environment selection is unavailable for the current
                    workspace or permissions.
                  </Typography>
                )}
              </Box>
            </Stack>
          ) : (
            <Stack spacing={2}>
              <Box>
                <Typography variant="subtitle1" fontWeight={600}>
                  Review your agent
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Create the definition now, then manage its deployment and runs
                  from the roster.
                </Typography>
              </Box>
              <Box
                sx={{
                  p: 2,
                  borderRadius: 1.5,
                  bgcolor: "background.neutral",
                  overflowWrap: "anywhere",
                }}
              >
                <Typography variant="h6">{draft.name.trim()}</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 1.5 }}
                >
                  {draft.role.trim()}
                </Typography>
                <Typography variant="subtitle2">Goal</Typography>
                <Typography
                  variant="body2"
                  sx={{ mt: 0.5, whiteSpace: "pre-wrap" }}
                >
                  {draft.purpose.trim()}
                </Typography>
                <Typography variant="subtitle2" sx={{ mt: 2 }}>
                  Responsibilities
                </Typography>
                {draft.responsibilities.trim() ? (
                  <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
                    {draft.responsibilities
                      .split("\n")
                      .map((line) => line.trim())
                      .filter(Boolean)
                      .map((line, index) => (
                        <Typography
                          component="li"
                          variant="body2"
                          key={`${index}-${line}`}
                        >
                          {line}
                        </Typography>
                      ))}
                  </Box>
                ) : (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 0.5 }}
                  >
                    No additional responsibilities.
                  </Typography>
                )}
              </Box>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Chip
                  size="small"
                  variant="outlined"
                  label={
                    draft.workMode === "always_on" ? "Always on" : "On demand"
                  }
                />
                <Chip
                  size="small"
                  variant="outlined"
                  label="No connections selected"
                />
                <Chip size="small" variant="outlined" label="No schedule" />
                <Chip
                  size="small"
                  variant="outlined"
                  label={environmentLabel}
                />
              </Stack>
              {draft.examples?.length ? (
                <Box>
                  <Typography variant="subtitle2">Example responses</Typography>
                  {draft.examples.map((example, index) => (
                    <Box key={index} sx={{ mt: 1, overflowWrap: "anywhere" }}>
                      <Typography
                        variant="body2"
                        sx={{ whiteSpace: "pre-wrap" }}
                      >
                        Prompt: {example.prompt}
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ whiteSpace: "pre-wrap" }}
                      >
                        Response: {example.response}
                      </Typography>
                    </Box>
                  ))}
                </Box>
              ) : null}
              <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
                Agent model:{" "}
                {draft.modelId ||
                  (isEdit
                    ? `Unchanged${draft.savedModelId ? ` · ${draft.savedModelId}` : ""}`
                    : `Runtime default${modelSelection?.defaultModelId ? ` · ${modelSelection.defaultModelId}` : ""}`)}
                .
              </Typography>
              <Typography variant="body2">
                Access:{" "}
                {sharingSupported
                  ? agentSharingLabel(draft.sharing)
                  : "Workspace default; sharing controls unavailable"}
                .
              </Typography>
              {!sharingValid ? (
                <Alert severity="warning">
                  The sharing selection can no longer be verified. Return to
                  details before saving.
                </Alert>
              ) : null}
              <Typography variant="body2">
                Skill revisions: {draft.skills?.length || 0}. Select skills
                separately in the destination workspace after importing a
                definition.
              </Typography>
              {draft.skills?.length ? (
                <Box component="ul" sx={{ pl: 2.5, overflowWrap: "anywhere" }}>
                  {draft.skills.map((ref) => (
                    <Typography component="li" variant="body2" key={ref.id}>
                      {skillSelection?.catalog?.find(
                        (entry) => entry.id === ref.id,
                      )?.name || ref.id}{" "}
                      · Revision {ref.revision}
                    </Typography>
                  ))}
                </Box>
              ) : null}
              {!skillsValid ? (
                <Alert severity="warning">
                  The selected skill revisions can no longer be verified. Return
                  to details to select or remove them.
                </Alert>
              ) : null}
              {!modelValid ? (
                <Alert severity="warning">
                  The model selection can no longer be verified. Return to
                  details to choose an available model. Your draft is kept here.
                </Alert>
              ) : null}
              {draft.environmentId &&
              environmentSelection?.exactRevision?.pinnedImageDigest ? (
                <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                  {environmentSelection.exactRevision.pinnedImageDigest}
                </Typography>
              ) : null}
              {!environmentValid ? (
                <Alert severity="warning">
                  The environment selection can no longer be verified. Return to
                  details to choose a revision. Your draft is kept here.
                </Alert>
              ) : null}
            </Stack>
          )}
          <Divider sx={{ my: 2 }} />
          <Stack direction="row" justifyContent="space-between" gap={1}>
            <Button onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Stack direction="row" spacing={1}>
              {!isEdit && step > 0 ? (
                <Button
                  disabled={busy}
                  onClick={() => {
                    setReplacement(null);
                    setStep(step - 1);
                  }}
                >
                  {step === 2 ? "Back to details" : "Change starter"}
                </Button>
              ) : null}
              {!isEdit && step === 0 && starter ? (
                <Button
                  onClick={() => {
                    setReplacement(null);
                    setStep(1);
                  }}
                >
                  Continue draft
                </Button>
              ) : null}
              {step > 0 ? (
                <Button
                  type="submit"
                  variant="contained"
                  disabled={!allowed || busy || !valid}
                >
                  {busy
                    ? "Saving remotely…"
                    : isEdit
                      ? "Save agent"
                      : step === 1
                        ? "Review agent"
                        : "Create agent"}
                </Button>
              ) : null}
            </Stack>
          </Stack>
        </Box>
      </CardContent>
    </Card>
  );
}

AgentEditor.propTypes = {
  mode: PropTypes.oneOf(["create", "edit"]).isRequired,
  draft: PropTypes.shape({
    name: PropTypes.string.isRequired,
    role: PropTypes.string.isRequired,
    purpose: PropTypes.string.isRequired,
    responsibilities: PropTypes.string.isRequired,
    workMode: PropTypes.string.isRequired,
    modelId: PropTypes.string,
    savedModelId: PropTypes.string,
    modelChanged: PropTypes.bool,
    skills: PropTypes.arrayOf(
      PropTypes.shape({ id: PropTypes.string, revision: PropTypes.number }),
    ),
    skillsChanged: PropTypes.bool,
    sharing: PropTypes.shape({
      mode: PropTypes.string,
      accountIds: PropTypes.arrayOf(PropTypes.string),
    }),
    sharingChanged: PropTypes.bool,
    importedDefinition: PropTypes.bool,
    examples: PropTypes.arrayOf(
      PropTypes.shape({
        prompt: PropTypes.string.isRequired,
        response: PropTypes.string.isRequired,
      }),
    ),
    examplesLoaded: PropTypes.bool,
    examplesChanged: PropTypes.bool,
    expectedVersion: PropTypes.number,
    environmentId: PropTypes.string,
    environmentRevision: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
    ]),
  }).isRequired,
  onChange: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  busy: PropTypes.bool.isRequired,
  error: PropTypes.string,
  allowed: PropTypes.bool,
  environmentSelection: PropTypes.shape({
    selected: PropTypes.object,
    environments: PropTypes.arrayOf(PropTypes.object),
    revisions: PropTypes.arrayOf(PropTypes.object),
    exactRevision: PropTypes.object,
    canSelect: PropTypes.bool,
    loading: PropTypes.bool,
    error: PropTypes.string,
    reload: PropTypes.func,
  }),
  environmentValid: PropTypes.bool,
  modelSelection: PropTypes.object,
  modelValid: PropTypes.bool,
  skillsSupported: PropTypes.bool,
  skillsAllowed: PropTypes.bool,
  skillSelection: PropTypes.object,
  skillsValid: PropTypes.bool,
  sharingSupported: PropTypes.bool,
  sharingCanManage: PropTypes.bool,
  sharingDirectory: PropTypes.object,
  sharingValid: PropTypes.bool,
  assistantClient: PropTypes.shape({ draftWorkforceAgent: PropTypes.func }),
};
