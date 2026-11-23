import { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { harnessIdempotencyKey } from "src/api/harness/harness";
import {
  useAcceptSourceReview,
  useCancelSourceReview,
  useEnvironmentSourceReview,
  useReprepareEnvironmentSourceReview,
} from "src/api/simulate-environments/sourceImports";
import { buildSourceReview } from "./importSourceReview";
import SourceScenarioGeneration from "./SourceScenarioGeneration";

const roles = {
  seed_records: "Structured scenario data",
  reference: "Reference",
  policy: "Policy",
};
const label = (value) => value?.replaceAll("_", " ");
const unfinished = ["reading", "needs_repair", "ready"];
const toggled = (items, value, checked) =>
  checked ? [...items, value] : items.filter((item) => item !== value);

function SourceReviewEditor({
  review,
  canEdit,
  acceptLabel,
  onAccepted,
  onCancelled,
  stale,
}) {
  const { id, state, revision, draft_digest: digest, preview, scope } = review;
  const accept = useAcceptSourceReview(onAccepted);
  const cancel = useCancelSourceReview(onCancelled);
  const reprepare = useReprepareEnvironmentSourceReview();
  const pending = accept.isPending || cancel.isPending || reprepare.isPending;
  const editable = canEdit && !pending && !stale;
  const error = accept.error || cancel.error || reprepare.error;
  const action = useRef();
  const sources = preview?.sources ?? [];
  const tables = preview?.destination_tables ?? [];
  const inferRepairs = scope.kind !== "environment" && !scope.target_table;
  const hasRepairControls =
    tables.length > 0 ||
    (inferRepairs && sources.some((item) => item.fields?.length));
  const mappings = preview?.proposals?.field_mappings ?? [];
  const rules = preview?.proposals?.policy_rules ?? [];
  const joins = preview?.proposals?.relationships ?? [];
  const [table, setTable] = useState("");
  const [repairs, setRepairs] = useState({});
  const [mappingIndexes, setMappingIndexes] = useState(() =>
    mappings.map((_, index) => index),
  );
  const [ruleIds, setRuleIds] = useState([]);
  const [joinIndexes, setJoinIndexes] = useState([]);
  const [testedAgent, setTestedAgent] = useState(false);
  const [personalDataModes, setPersonalDataModes] = useState(() =>
    Object.fromEntries(
      mappings
        .filter(({ mapping }) => mapping.personal_data)
        .map(({ source_id }) => [source_id, "synthetic_replace"]),
    ),
  );
  const pins = { reviewId: id, expected_revision: revision };
  const keyFor = (body) => {
    const signature = JSON.stringify(body);
    if (action.current?.signature !== signature) {
      action.current = { signature, key: harnessIdempotencyKey() };
    }
    return action.current.key;
  };
  const repairBody = table
    ? { target_table: table }
    : {
        schema_repairs: Object.values(repairs).filter(
          (item) =>
            item.text_fields?.length ||
            item.identity_fields?.length ||
            item.separate_entity ||
            item.manual_relationships,
        ),
      };
  const canRepair =
    state === "needs_repair" &&
    (tables.length
      ? tables.includes(table)
      : Boolean(repairBody.schema_repairs?.length));
  const canAccept =
    state === "ready" && Boolean(digest && scope?.schema_digest);

  const doRepair = () =>
    reprepare.mutate({
      ...pins,
      ...repairBody,
      idempotency_key: keyFor(repairBody),
    });
  const doAccept = () => {
    const body = {
      expected_draft_digest: digest,
      expected_schema_digest: scope.schema_digest,
      decisions: buildSourceReview(preview, {
        acceptedMappingIndexes: mappingIndexes,
        acceptedRuleIds: ruleIds,
        acceptedRelationshipIndexes: joinIndexes,
        testedAgent,
        personalDataModes,
      }),
    };
    accept.mutate({ ...pins, ...body, idempotency_key: keyFor(body) });
  };
  const changeRepair = (sourceId, field, value) =>
    setRepairs((current) => ({
      ...current,
      [sourceId]: { ...current[sourceId], source_id: sourceId, [field]: value },
    }));

  return (
    <Stack spacing={2}>
      <Typography variant="h6">Review sources</Typography>
      {state === "reading" && (
        <Stack direction="row" alignItems="center" spacing={1} role="status">
          <CircularProgress size={16} />
          <Typography>Reading sources…</Typography>
        </Stack>
      )}
      {reprepare.isPending && (
        <Typography role="status">
          Preparing the revised source preview…
        </Typography>
      )}
      {state === "accepted" && (
        <Alert severity="success">Source review accepted.</Alert>
      )}
      {state === "cancelled" && (
        <Alert severity="info">Source review cancelled.</Alert>
      )}
      {state === "expired" && (
        <Alert severity="warning">
          Source review expired. Prepare a new review to use these sources.
        </Alert>
      )}
      {stale && unfinished.includes(state) && (
        <Alert severity="warning">
          The Environment changed. Cancel this review and prepare the sources
          again.
        </Alert>
      )}
      {error && (
        <Alert severity="error">
          {error.message ||
            error.detail ||
            "The source review could not be updated."}
        </Alert>
      )}
      <Stack
        component="fieldset"
        disabled={!editable}
        spacing={2}
        sx={{ border: 0, m: 0, p: 0, minWidth: 0 }}
      >
        {tables.length > 0 && (
          <TextField
            select
            size="small"
            label="Scenario data table"
            value={table}
            disabled={!editable}
            onChange={(event) => setTable(event.target.value)}
          >
            {tables.map((name) => (
              <MenuItem key={name} value={name}>
                {name}
              </MenuItem>
            ))}
          </TextField>
        )}
        {sources.map(({ pin, filename, fields = [], row_count: rows }) => (
          <Stack key={pin.source_id} spacing={1}>
            <Typography fontWeight={600}>{filename}</Typography>
            <Typography color="text.secondary">
              {roles[pin.role] || pin.role}
              {rows > 0 ? ` · ${rows} rows` : ""}
            </Typography>
            {state === "needs_repair" &&
              inferRepairs &&
              !tables.length &&
              fields.length > 0 && (
                <>
                  {[
                    ["text_fields", "Treat as text"],
                    ["identity_fields", "Identify records by"],
                  ].map(([key, title]) => (
                    <TextField
                      key={key}
                      select
                      size="small"
                      label={title}
                      disabled={!editable}
                      SelectProps={{ multiple: true }}
                      value={repairs[pin.source_id]?.[key] || []}
                      onChange={(event) =>
                        changeRepair(pin.source_id, key, event.target.value)
                      }
                    >
                      {fields.map(({ source_field: name }) => (
                        <MenuItem key={name} value={name}>
                          {name}
                        </MenuItem>
                      ))}
                    </TextField>
                  ))}
                  {[
                    ["separate_entity", "Use a separate table for this source"],
                    ["manual_relationships", "Review relationships manually"],
                  ].map(([key, title]) => (
                    <FormControlLabel
                      key={key}
                      label={title}
                      control={
                        <Checkbox
                          checked={Boolean(repairs[pin.source_id]?.[key])}
                          onChange={(event) =>
                            changeRepair(
                              pin.source_id,
                              key,
                              event.target.checked,
                            )
                          }
                        />
                      }
                    />
                  ))}
                </>
              )}
          </Stack>
        ))}
        {state === "ready" && (
          <>
            {mappings.map(({ source_id: sourceId, mapping }, index) => (
              <FormControlLabel
                key={`${sourceId}-${mapping.source_field}-${index}`}
                label={`${mapping.source_field} → ${mapping.target_field}`}
                control={
                  <Checkbox
                    checked={mappingIndexes.includes(index)}
                    onChange={(event) =>
                      setMappingIndexes(
                        toggled(mappingIndexes, index, event.target.checked),
                      )
                    }
                  />
                }
              />
            ))}
            {Object.entries(personalDataModes).map(([id, mode]) => (
              <FormControlLabel
                key={id}
                label={`Replace personal data in ${sources.find((item) => item.pin.source_id === id)?.filename || id} with synthetic values`}
                control={
                  <Switch
                    checked={mode === "synthetic_replace"}
                    onChange={(event) =>
                      setPersonalDataModes({
                        ...personalDataModes,
                        [id]: event.target.checked
                          ? "synthetic_replace"
                          : "none",
                      })
                    }
                  />
                }
              />
            ))}
            {joins.map(({ relationship }, index) => (
              <FormControlLabel
                key={index}
                label={`${relationship.from_field} → ${relationship.to_field}`}
                control={
                  <Checkbox
                    checked={joinIndexes.includes(index)}
                    onChange={(event) =>
                      setJoinIndexes(
                        toggled(joinIndexes, index, event.target.checked),
                      )
                    }
                  />
                }
              />
            ))}
            {rules.map((rule) => {
              const statement = sources
                .flatMap((source) => source.statements || [])
                .find(
                  ({ statement_id: statementId }) =>
                    statementId === rule.statement_id,
                );
              return (
                <FormControlLabel
                  key={rule.rule_id}
                  label={[
                    rule.statement_id,
                    label(rule.status),
                    label(rule.kind),
                    label(rule.check_type),
                    statement?.page != null && `Page ${statement.page}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  control={
                    <Checkbox
                      disabled={rule.status !== "supported"}
                      checked={ruleIds.includes(rule.statement_id)}
                      onChange={(event) =>
                        setRuleIds(
                          toggled(
                            ruleIds,
                            rule.statement_id,
                            event.target.checked,
                          ),
                        )
                      }
                    />
                  }
                />
              );
            })}
            <FormControlLabel
              label="Make approved source facts visible to the tested agent"
              control={
                <Switch
                  checked={testedAgent}
                  onChange={(event) => setTestedAgent(event.target.checked)}
                />
              }
            />
          </>
        )}
      </Stack>
      {(preview?.diagnostics || [])
        .filter(
          ({ code }) => code !== "source_schema_ambiguous" || !tables.length,
        )
        .map((item, index) => (
          <Alert
            key={`${item.code}-${index}`}
            severity={item.severity === "blocking" ? "error" : "warning"}
          >
            {item.message || label(item.code)}
          </Alert>
        ))}
      {unfinished.includes(state) && (
        <Stack direction="row" spacing={1}>
          <Button
            disabled={!canEdit || pending}
            onClick={() =>
              cancel.mutate({ ...pins, expected_draft_digest: digest || null })
            }
          >
            Cancel review
          </Button>
          {state === "needs_repair" && hasRepairControls && (
            <Button
              variant="contained"
              disabled={!editable || !canRepair}
              onClick={doRepair}
            >
              {tables.length ? "Prepare for this table" : "Prepare again"}
            </Button>
          )}
          {state === "ready" && (
            <Button
              variant="contained"
              disabled={!editable || !canAccept}
              onClick={doAccept}
            >
              {acceptLabel}
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
}

SourceReviewEditor.propTypes = {
  review: PropTypes.object.isRequired,
  acceptLabel: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  onAccepted: PropTypes.func,
  onCancelled: PropTypes.func,
  stale: PropTypes.bool,
};

export default function SourceReviewPanel({
  reviewId,
  ownerId,
  environmentBasis,
  onDismiss,
  ...editor
}) {
  const query = useEnvironmentSourceReview(reviewId);
  const review = query.data;
  if (query.isError) {
    return (
      <Stack spacing={1}>
        <Alert severity="error">
          {query.error?.message || "The source review could not be opened."}
        </Alert>
        <Box>
          <Button onClick={query.refetch}>Retry</Button>
          {onDismiss && <Button onClick={onDismiss}>Close</Button>}
        </Box>
      </Stack>
    );
  }
  if (!review)
    return <Typography role="status">Opening source review…</Typography>;
  if (review.scope.id !== ownerId) {
    return (
      <Alert severity="error">
        This review belongs to a different Environment.
      </Alert>
    );
  }
  const stale =
    environmentBasis &&
    (review.scope.environment_version_id !==
      environmentBasis.environment_version_id ||
      review.scope.draft?.id !== environmentBasis.draft?.id ||
      review.scope.draft?.revision !== environmentBasis.draft?.revision);
  return (
    <>
      <SourceReviewEditor
        key={`${review.id}:${review.revision}`}
        review={review}
        stale={stale}
        {...editor}
      />
      {review.state === "accepted" && review.receipt?.scenario_generation ? (
        <SourceScenarioGeneration review={review} onDismiss={onDismiss} />
      ) : onDismiss && !unfinished.includes(review.state) ? (
        <Button onClick={onDismiss}>Close</Button>
      ) : null}
    </>
  );
}

SourceReviewPanel.propTypes = {
  reviewId: PropTypes.string.isRequired,
  ownerId: PropTypes.string.isRequired,
  environmentBasis: PropTypes.object,
  acceptLabel: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  onAccepted: PropTypes.func,
  onCancelled: PropTypes.func,
  onDismiss: PropTypes.func,
};
