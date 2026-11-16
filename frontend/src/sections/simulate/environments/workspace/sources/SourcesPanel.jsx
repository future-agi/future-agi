import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import {
  useAcceptSource,
  useEnvironmentSources,
  usePrepareKbSource,
  usePrepareUploadSource,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import { buildSourceReview } from "./sourceReview";

const errorText = (error) =>
  error?.message ||
  error?.detail ||
  error?.error ||
  "The source operation failed.";

export default function SourcesPanel({ env, locked }) {
  const location = useLocation();
  const navigate = useNavigate();
  const uploadInput = useRef(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [prepared, setPrepared] = useState(null);
  const [testedAgent, setTestedAgent] = useState(false);
  const [acceptedMappingIndexes, setAcceptedMappingIndexes] = useState([]);
  const [acceptedRuleIds, setAcceptedRuleIds] = useState([]);
  const [acceptedRelationshipIndexes, setAcceptedRelationshipIndexes] =
    useState([]);
  const [personalDataModes, setPersonalDataModes] = useState({});
  const [uploadPurpose, setUploadPurpose] = useState("reference");
  const [message, setMessage] = useState("");
  const { data: summary, isLoading: summaryLoading } = useEnvironmentSources(
    env.id,
  );
  const { data: capabilities } = useSourceCapabilities(env.id);
  const { data: knowledgeBases = [], isLoading: knowledgeLoading } =
    useKnowledgeBaseList();
  const prepareKb = usePrepareKbSource(env.id);
  const prepareUpload = usePrepareUploadSource(env.id);
  const accept = useAcceptSource(env.id);

  const pending =
    prepareKb.isPending || prepareUpload.isPending || accept.isPending;
  const operationError = prepareKb.error || prepareUpload.error || accept.error;
  const blocking = (prepared?.draft?.diagnostics || []).some(
    (diagnostic) => diagnostic.severity === "blocking",
  );
  const preparedSources = prepared?.draft?.sources || [];
  const proposals = prepared?.draft?.proposals || {};

  const openPrepared = useCallback((result) => {
    const mappings = result?.draft?.proposals?.field_mappings || [];
    const modes = {};
    mappings.forEach((proposal) => {
      if (proposal.mapping?.personal_data) {
        modes[proposal.source_id] = "synthetic_replace";
      }
    });
    setTestedAgent(false);
    setAcceptedMappingIndexes(mappings.map((_, index) => index));
    setAcceptedRuleIds([]);
    setAcceptedRelationshipIndexes([]);
    setPersonalDataModes(modes);
    setPrepared(result);
  }, []);

  const completedKnowledgeBases = useMemo(
    () =>
      knowledgeBases.filter(
        (item) => (item.status || item.processing_status) === "Completed",
      ),
    [knowledgeBases],
  );

  const prepareSelection = useCallback(
    (selection) => {
      if (!capabilities) return;
      setMessage("");
      prepareKb.mutate(
        {
          expected_environment_version_id: capabilities.environment_version_id,
          expected_draft: capabilities.draft,
          selection: { kind: "knowledge_base", ...selection },
        },
        {
          onSuccess: (result) => {
            openPrepared(result);
            setPickerOpen(false);
            if (location.state?.environmentSourceSelection) {
              navigate(`${location.pathname}?tab=sources`, {
                replace: true,
                state: {},
              });
            }
          },
        },
      );
    },
    [
      capabilities,
      location.pathname,
      location.state,
      navigate,
      openPrepared,
      prepareKb,
    ],
  );

  useEffect(() => {
    const selection = location.state?.environmentSourceSelection;
    if (!selection || !capabilities || prepared || prepareKb.isPending) return;
    prepareSelection(selection);
  }, [
    capabilities,
    location.pathname,
    location.state,
    prepareKb.isPending,
    prepareSelection,
    prepared,
  ]);

  const onUpload = (event) => {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (!files.length || !capabilities) return;
    const extensions = files.map((file) =>
      file.name.split(".").pop()?.toLowerCase(),
    );
    const purpose = extensions.every((extension) =>
      ["csv", "json"].includes(extension),
    )
      ? "seed_records"
      : extensions.every((extension) => extension === "txt")
        ? "reference"
        : uploadPurpose;
    setMessage("");
    prepareUpload.mutate(
      {
        files,
        purpose,
        versionId: capabilities.environment_version_id,
        draft: capabilities.draft,
      },
      { onSuccess: openPrepared },
    );
  };

  const acceptPrepared = () => {
    const basis = prepared.capabilities;
    accept.mutate(
      {
        import_ref: prepared.import_ref,
        expected_environment_version_id: basis.environment_version_id,
        expected_draft: basis.draft,
        expected_draft_digest: prepared.draft_digest,
        review: buildSourceReview(prepared.draft, {
          testedAgent,
          acceptedMappingIndexes,
          acceptedRuleIds,
          acceptedRelationshipIndexes,
          personalDataModes,
        }),
      },
      {
        onSuccess: (result) => {
          setPrepared(null);
          setMessage(
            `Source added to the Environment draft. Validation: ${
              result.validation?.status || "not reported"
            }. Use Save to create a version.`,
          );
        },
      },
    );
  };

  return (
    <Stack spacing={3} sx={{ p: 3, maxWidth: 960 }}>
      <Box>
        <Typography variant="h5">Sources</Typography>
        <Typography color="text.secondary">
          Add files or Knowledge Base content to this Environment. Nothing
          becomes live until you save the draft.
        </Typography>
      </Box>

      {message && <Alert severity="success">{message}</Alert>}
      {operationError && (
        <Alert severity="error">{errorText(operationError)}</Alert>
      )}
      {!capabilities?.participant_visibility_enabled && (
        <Alert severity="info">
          {capabilities?.participant_visibility_reason ||
            "Participant-specific visibility is unavailable."}
        </Alert>
      )}

      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={1.5}
        alignItems="center"
      >
        <FormControl size="small" sx={{ minWidth: 170 }}>
          <InputLabel id="upload-purpose-label">Upload purpose</InputLabel>
          <Select
            labelId="upload-purpose-label"
            label="Upload purpose"
            value={uploadPurpose}
            onChange={(event) => setUploadPurpose(event.target.value)}
            disabled={locked || pending}
          >
            <MenuItem value="reference">Reference</MenuItem>
            <MenuItem value="policy">Policy PDF</MenuItem>
            <MenuItem value="seed_records">Seed records</MenuItem>
          </Select>
        </FormControl>
        <input
          ref={uploadInput}
          hidden
          type="file"
          multiple
          accept=".pdf,.txt,.csv,.json"
          onChange={onUpload}
        />
        <Button
          variant="contained"
          disabled={locked || pending || !capabilities?.upload?.enabled}
          onClick={() => uploadInput.current?.click()}
        >
          Upload file
        </Button>
        <Button
          variant="outlined"
          disabled={locked || pending || !capabilities?.knowledge_base?.enabled}
          onClick={() => setPickerOpen(true)}
        >
          Select Knowledge Base
        </Button>
      </Stack>

      <Box>
        <Typography variant="h6" sx={{ mb: 1 }}>
          Current sources
        </Typography>
        {summaryLoading ? (
          <Typography color="text.secondary">Loading sources…</Typography>
        ) : summary?.sources?.length ? (
          <Stack spacing={1}>
            {summary.sources.map((source) => (
              <Stack
                key={source.source_id}
                direction="row"
                spacing={1}
                alignItems="center"
              >
                <Typography>{source.source_id}</Typography>
                <Chip size="small" label={source.purpose} />
                {summary.draft && (
                  <Chip size="small" color="warning" label="Draft" />
                )}
              </Stack>
            ))}
          </Stack>
        ) : (
          <Typography color="text.secondary">No sources added yet.</Typography>
        )}
      </Box>

      <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} fullWidth>
        <DialogTitle>Select a Knowledge Base</DialogTitle>
        <DialogContent dividers>
          {knowledgeLoading ? (
            <Typography>Loading…</Typography>
          ) : completedKnowledgeBases.length ? (
            <List>
              {completedKnowledgeBases.map((item) => (
                <ListItemButton
                  key={item.id}
                  onClick={() =>
                    prepareSelection({ kb_id: item.id, whole_collection: true })
                  }
                >
                  <ListItemText
                    primary={item.name}
                    secondary="Use the whole collection"
                  />
                </ListItemButton>
              ))}
            </List>
          ) : (
            <Typography color="text.secondary">
              No completed Knowledge Bases are available.
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPickerOpen(false)}>Cancel</Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={Boolean(prepared)}
        onClose={() => !pending && setPrepared(null)}
        fullWidth
      >
        <DialogTitle>Review source use</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            {accept.error && (
              <Alert severity="error">{errorText(accept.error)}</Alert>
            )}
            {preparedSources.map((source) => (
              <Box key={source.pin.source_id}>
                <Typography fontWeight={600}>{source.filename}</Typography>
                <Typography color="text.secondary">
                  Proposed purpose: {source.pin.role}
                </Typography>
              </Box>
            ))}
            {(proposals.field_mappings || []).length > 0 && (
              <Box>
                <Typography fontWeight={600}>Field mappings</Typography>
                {(proposals.field_mappings || []).map((proposal, index) => (
                  <FormControlLabel
                    key={`${proposal.source_id}-${proposal.mapping.source_field}-${index}`}
                    control={
                      <Checkbox
                        checked={acceptedMappingIndexes.includes(index)}
                        onChange={(event) =>
                          setAcceptedMappingIndexes((current) =>
                            event.target.checked
                              ? [...current, index]
                              : current.filter((item) => item !== index),
                          )
                        }
                      />
                    }
                    label={`${proposal.source_id}: ${proposal.mapping.source_field} → ${proposal.mapping.target_field}`}
                  />
                ))}
              </Box>
            )}
            {Object.entries(personalDataModes).map(([sourceId, mode]) => (
              <FormControlLabel
                key={sourceId}
                control={
                  <Switch
                    checked={mode === "synthetic_replace"}
                    onChange={(event) =>
                      setPersonalDataModes((current) => ({
                        ...current,
                        [sourceId]: event.target.checked
                          ? "synthetic_replace"
                          : "none",
                      }))
                    }
                  />
                }
                label={`Replace personal data in ${sourceId} with synthetic equivalents`}
              />
            ))}
            {(proposals.relationships || []).length > 0 && (
              <Box>
                <Typography fontWeight={600}>Relationships</Typography>
                {(proposals.relationships || []).map((proposal, index) => {
                  const relationship = proposal.relationship;
                  return (
                    <FormControlLabel
                      key={`${relationship.from_source_id}-${relationship.from_field}-${index}`}
                      control={
                        <Checkbox
                          checked={acceptedRelationshipIndexes.includes(index)}
                          onChange={(event) =>
                            setAcceptedRelationshipIndexes((current) =>
                              event.target.checked
                                ? [...current, index]
                                : current.filter((item) => item !== index),
                            )
                          }
                        />
                      }
                      label={`${relationship.from_source_id}.${relationship.from_field} → ${relationship.to_source_id}.${relationship.to_field}`}
                    />
                  );
                })}
              </Box>
            )}
            {(proposals.policy_rules || []).length > 0 && (
              <Box>
                <Typography fontWeight={600}>Policy rules</Typography>
                {(proposals.policy_rules || []).map((proposal) => (
                  <FormControlLabel
                    key={proposal.statement_id}
                    control={
                      <Checkbox
                        checked={acceptedRuleIds.includes(proposal.statement_id)}
                        disabled={proposal.status !== "supported"}
                        onChange={(event) =>
                          setAcceptedRuleIds((current) =>
                            event.target.checked
                              ? [...current, proposal.statement_id]
                              : current.filter(
                                  (item) => item !== proposal.statement_id,
                                ),
                          )
                        }
                      />
                    }
                    label={`${proposal.statement_id}: ${proposal.status}`}
                  />
                ))}
              </Box>
            )}
            <FormControlLabel
              control={
                <Switch
                  checked={testedAgent}
                  onChange={(event) => setTestedAgent(event.target.checked)}
                />
              }
              label={
                testedAgent
                  ? "Visible to the tested agent"
                  : "Evaluator-only (recommended for sensitive data)"
              }
            />
            {(prepared?.draft?.diagnostics || []).map((diagnostic, index) => (
              <Alert
                key={`${diagnostic.code}-${index}`}
                severity={
                  diagnostic.severity === "blocking" ? "error" : "warning"
                }
              >
                {diagnostic.message}
              </Alert>
            ))}
            <Typography variant="body2" color="text.secondary">
              Only the checked mappings, relationships, and supported policy
              rules will be added. Blocking diagnostics must be fixed before
              this source can be added.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={pending} onClick={() => setPrepared(null)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            disabled={pending || blocking}
            onClick={acceptPrepared}
          >
            Add to draft
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

SourcesPanel.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  locked: PropTypes.bool,
};
