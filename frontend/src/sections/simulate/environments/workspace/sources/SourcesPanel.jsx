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
  useEnvironmentSourceReview,
  useEnvironmentSources,
  usePrepareKbSource,
  usePrepareUploadSource,
  useReprepareEnvironmentSourceReview,
  useSourceCapabilities,
} from "src/api/simulate-environments/sourceImports";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { buildSourceReview } from "./importSourceReview";

const errorText = (error) =>
  error?.message ||
  error?.detail ||
  error?.error ||
  "The source operation failed.";

const mebibytes = (bytes) => `${bytes / (1024 * 1024)} MiB`;

const sourcePurposeLabels = {
  reference: "Reference",
  policy: "Policy",
  seed_records: "Structured scenario data",
};

const preparedFromReview = (review) => ({
  import_ref: review.id,
  revision: review.revision,
  draft_digest: review.draft_digest,
  draft: review.preview,
  capabilities: review.scope,
});

export default function SourcesPanel({ env, locked }) {
  const location = useLocation();
  const navigate = useNavigate();
  const uploadInput = useRef(null);
  const consumedSelection = useRef(null);
  const { canEdit } = useSimulationAccess();
  const readOnly = locked || !canEdit;
  const [pickerOpen, setPickerOpen] = useState(false);
  const reviewStorageKey = `harness-environment-source-review:${env.id}`;
  const [restoreId, setRestoreId] = useState(() =>
    sessionStorage.getItem(reviewStorageKey),
  );
  const [prepared, setPrepared] = useState(null);
  const [targetTable, setTargetTable] = useState("");
  const [testedAgent, setTestedAgent] = useState(false);
  const [acceptedMappingIndexes, setAcceptedMappingIndexes] = useState([]);
  const [acceptedRuleIds, setAcceptedRuleIds] = useState([]);
  const [acceptedRelationshipIndexes, setAcceptedRelationshipIndexes] =
    useState([]);
  const [personalDataModes, setPersonalDataModes] = useState({});
  const [uploadPurpose, setUploadPurpose] = useState("automatic");
  const [message, setMessage] = useState("");
  const {
    data: summary,
    isLoading: summaryLoading,
    error: summaryError,
  } = useEnvironmentSources(env.id);
  const { data: capabilities, error: capabilitiesError } =
    useSourceCapabilities(env.id);
  const {
    data: restored,
    error: restoreError,
    isFetching: restoreFetching,
  } = useEnvironmentSourceReview(restoreId);
  const { data: knowledgeBases = [], isLoading: knowledgeLoading } =
    useKnowledgeBaseList();
  const prepareKb = usePrepareKbSource(env.id);
  const prepareUpload = usePrepareUploadSource(env.id);
  const reprepare = useReprepareEnvironmentSourceReview();
  const accept = useAcceptSource(env.id);

  const restoring = Boolean(restoreId && !restoreError);
  const pending =
    restoring ||
    prepareKb.isPending ||
    prepareUpload.isPending ||
    reprepare.isPending ||
    accept.isPending;
  const reviewError =
    prepareKb.error || prepareUpload.error || reprepare.error || accept.error;
  const operationError =
    summaryError || capabilitiesError || (!prepared && reviewError);
  const staleReview =
    prepared &&
    capabilities &&
    (prepared.capabilities.environment_version_id !==
      capabilities.environment_version_id ||
      prepared.capabilities.draft?.id !== capabilities.draft?.id ||
      prepared.capabilities.draft?.revision !== capabilities.draft?.revision);
  const blocking = (prepared?.draft?.diagnostics || []).some(
    (diagnostic) => diagnostic.severity === "blocking",
  );
  const preparedSources = prepared?.draft?.sources || [];
  const proposals = prepared?.draft?.proposals || {};
  const destinationTables = prepared?.draft?.destination_tables || [];
  const canAccept = Boolean(
    capabilities &&
      prepared?.draft_digest &&
      !readOnly &&
      !pending &&
      !blocking &&
      !staleReview,
  );

  const closePrepared = useCallback(() => {
    sessionStorage.removeItem(reviewStorageKey);
    setRestoreId(null);
    setPrepared(null);
    setTargetTable("");
  }, [reviewStorageKey]);

  const openPrepared = useCallback(
    (result) => {
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
      setTargetTable("");
      sessionStorage.setItem(reviewStorageKey, result.import_ref);
      setRestoreId(null);
      setPrepared(result);
    },
    [reviewStorageKey],
  );

  useEffect(() => {
    if (!restored || restoreFetching || restoreError) return;
    if (
      restored.scope.kind !== "environment" ||
      restored.scope.id !== env.id ||
      !["ready", "needs_repair"].includes(restored.state)
    ) {
      closePrepared();
      return;
    }
    openPrepared(preparedFromReview(restored));
  }, [
    closePrepared,
    env.id,
    openPrepared,
    restoreError,
    restoreFetching,
    restored,
  ]);

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
              navigate(`${location.pathname}?tab=world`, {
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
    if (
      readOnly ||
      !selection ||
      !capabilities ||
      prepared ||
      restoring ||
      prepareKb.isPending ||
      consumedSelection.current === location.key
    )
      return;
    consumedSelection.current = location.key;
    prepareSelection(selection);
  }, [
    capabilities,
    location.key,
    location.state,
    readOnly,
    prepareKb.isPending,
    prepareSelection,
    prepared,
    restoring,
  ]);

  const onUpload = (event) => {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (!files.length || !capabilities) return;
    setMessage("");
    prepareUpload.mutate(
      {
        files,
        purpose: uploadPurpose === "policy" ? "policy" : undefined,
        versionId: capabilities.environment_version_id,
        draft: capabilities.draft,
      },
      { onSuccess: openPrepared },
    );
  };

  const prepareForTable = () => {
    const { import_ref: reviewId, revision } = prepared;
    const tableIndex = destinationTables.indexOf(targetTable);
    reprepare.mutate(
      {
        reviewId,
        expected_revision: revision,
        target_table: targetTable,
        idempotency_key: `${reviewId}:${revision}:${tableIndex}`,
      },
      { onSuccess: (result) => openPrepared(preparedFromReview(result)) },
    );
  };

  const acceptPrepared = () => {
    if (!canAccept) return;
    const basis = prepared.capabilities;
    accept.mutate(
      {
        import_ref: prepared.import_ref,
        expected_environment_version_id: basis.environment_version_id,
        expected_draft: basis.draft,
        expected_draft_digest: prepared.draft_digest,
        expected_revision: prepared.revision,
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
          const affected = result.validation?.affected_scenarios?.length || 0;
          closePrepared();
          setMessage(
            "Source added to the Environment draft. Save creates a new Environment version." +
              (affected
                ? ` Review ${affected} existing scenario${affected === 1 ? "" : "s"} after saving.`
                : ""),
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
          Use files and Knowledge Base content as input for scenarios. Adding a
          source updates the draft. Save creates a new Environment version and
          preserves the original version and source files.
        </Typography>
      </Box>

      {message && <Alert severity="success">{message}</Alert>}
      {operationError && (
        <Alert severity="error">{errorText(operationError)}</Alert>
      )}
      {restoreError && (
        <Alert severity="warning">
          The saved review could not be opened. Prepare the sources again.
        </Alert>
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
            disabled={readOnly || pending}
          >
            <MenuItem value="automatic">Automatic by file type</MenuItem>
            <MenuItem value="policy">Policy PDF</MenuItem>
          </Select>
        </FormControl>
        <input
          ref={uploadInput}
          hidden
          type="file"
          aria-label="Environment source files"
          multiple
          accept={uploadPurpose === "policy" ? ".pdf" : ".pdf,.txt,.csv,.json"}
          onChange={onUpload}
        />
        <Button
          variant="contained"
          disabled={readOnly || pending || !capabilities?.upload?.enabled}
          onClick={() => uploadInput.current?.click()}
        >
          Upload file
        </Button>
        <Button
          variant="outlined"
          disabled={
            readOnly || pending || !capabilities?.knowledge_base?.enabled
          }
          onClick={() => setPickerOpen(true)}
        >
          Select Knowledge Base
        </Button>
      </Stack>

      {capabilities && (
        <Typography variant="body2" color="text.secondary">
          CSV and JSON are used as structured scenario data. PDF and TXT can be
          used as references. Upload:{" "}
          {capabilities.upload.formats.join(", ").toUpperCase()}, up to{" "}
          {capabilities.upload.max_files} files,{" "}
          {mebibytes(capabilities.upload.max_file_bytes)} per file and{" "}
          {mebibytes(capabilities.upload.max_total_bytes)} total. Knowledge
          Base: {capabilities.knowledge_base.formats.join(", ").toUpperCase()},
          up to {capabilities.knowledge_base.max_files} files,{" "}
          {mebibytes(capabilities.knowledge_base.max_file_bytes)} per file and{" "}
          {mebibytes(capabilities.knowledge_base.max_total_bytes)} total.
        </Typography>
      )}

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
                <Chip
                  size="small"
                  label={sourcePurposeLabels[source.purpose] || source.purpose}
                />
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
        onClose={() => !pending && closePrepared()}
        fullWidth
      >
        <DialogTitle>Review source use</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            {reviewError && (
              <Alert severity="error">{errorText(reviewError)}</Alert>
            )}
            {destinationTables.length > 0 && (
              <FormControl size="small">
                <InputLabel id="source-destination-table-label">
                  Scenario data table
                </InputLabel>
                <Select
                  labelId="source-destination-table-label"
                  label="Scenario data table"
                  value={targetTable}
                  disabled={readOnly || pending || staleReview}
                  onChange={(event) => setTargetTable(event.target.value)}
                >
                  {destinationTables.map((table) => (
                    <MenuItem key={table} value={table}>
                      {table}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            {preparedSources.map((source) => (
              <Box key={source.pin.source_id}>
                <Typography fontWeight={600}>{source.filename}</Typography>
                <Typography color="text.secondary">
                  {sourcePurposeLabels[source.pin.role] || source.pin.role}
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
                        checked={acceptedRuleIds.includes(
                          proposal.statement_id,
                        )}
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
            {!destinationTables.length && (
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
            )}
            {(prepared?.draft?.diagnostics || [])
              .filter(
                (diagnostic) => diagnostic.code !== "source_schema_ambiguous",
              )
              .map((diagnostic, index) => (
                <Alert
                  key={`${diagnostic.code}-${index}`}
                  severity={
                    diagnostic.severity === "blocking" ? "error" : "warning"
                  }
                >
                  {diagnostic.message || diagnostic.code}
                </Alert>
              ))}
            {staleReview && (
              <Alert severity="warning">
                The Environment changed. Close this review and prepare the
                sources again.
              </Alert>
            )}
            {!destinationTables.length && (
              <Typography variant="body2" color="text.secondary">
                Only the checked mappings, relationships, and supported policy
                rules will be added. Blocking diagnostics must be fixed before
                this source can be added.
              </Typography>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button disabled={pending} onClick={closePrepared}>
            Cancel
          </Button>
          {destinationTables.length > 0 ? (
            <Button
              variant="contained"
              disabled={
                !capabilities ||
                readOnly ||
                pending ||
                staleReview ||
                !targetTable
              }
              onClick={prepareForTable}
            >
              Prepare for this table
            </Button>
          ) : (
            <Button
              variant="contained"
              disabled={!canAccept}
              onClick={acceptPrepared}
            >
              Add to draft
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

SourcesPanel.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  locked: PropTypes.bool,
};
