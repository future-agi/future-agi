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
import { buildSourceReview, readableField } from "./importSourceReview";
import PersonalDataChoice from "./PersonalDataChoice";
import {
  ReviewedColumns,
  ReviewedRecords,
  ReviewedStatements,
  SourceRepairs,
} from "./ReviewedSource";
import SourceScenarioGeneration from "./SourceScenarioGeneration";

const roles = {
  seed_records: "Structured scenario data",
  reference: "Reference",
  policy: "Policy",
};
const diagnosticMessages = {
  source_parse_failed:
    "Some data could not be read. Check the file format and the rows shown above.",
  source_no_extractable_text:
    "This document has no readable text. Upload a text-based copy.",
  source_parser_dependency_missing: "The document reader is unavailable.",
  source_too_large: "This file exceeds the supported size.",
  source_unsupported_type: "This file format is unsupported.",
  source_content_flagged:
    "Content review blocked this file. Check its contents before uploading again.",
  source_privacy_unresolved:
    "Personal data needs review before this file can be used.",
  source_schema_ambiguous: "Choose the table to use for these records.",
  source_schema_needs_repair: "Review the column types and row identifier.",
};
const unfinished = ["reading", "needs_repair", "ready"];
const toggled = (items, value, checked) =>
  checked ? [...items, value] : items.filter((item) => item !== value);

function SourceReviewEditor({
  review,
  canEdit,
  acceptLabel,
  onAccepted,
  onCancelled,
  defaultPersonalDataMode,
  stale,
}) {
  const { id, state, revision, draft_digest: digest, preview, scope } = review;
  const accept = useAcceptSourceReview(onAccepted);
  const cancel = useCancelSourceReview(onCancelled);
  const reprepare = useReprepareEnvironmentSourceReview();
  const pending = accept.isPending || cancel.isPending || reprepare.isPending;
  const editable = canEdit && !pending && !stale && unfinished.includes(state);
  const error = accept.error || cancel.error || reprepare.error;
  const action = useRef();
  const sources = preview?.sources ?? [];
  const tables = preview?.destination_tables ?? scope.destination_tables ?? [];
  const inferRepairs = scope.kind !== "environment" && !scope.target_table;
  const diagnostics = preview?.diagnostics || [];
  const privateTextIds = new Set(
    sources
      .filter((s) => s.pin.role !== "seed_records")
      .map((s) => s.pin.source_id),
  );
  const privateText = new Set(
    diagnostics
      .filter(
        (d) =>
          d.code === "source_privacy_unresolved" &&
          privateTextIds.has(d.span?.source_id),
      )
      .map((d) => d.span?.source_id),
  );
  const [redactions, setRedactions] = useState([]);
  const hasRepairControls =
    tables.length > 0 ||
    (inferRepairs && sources.some((item) => item.fields?.length));
  const mappings = preview?.proposals?.field_mappings ?? [];
  const rules = preview?.proposals?.policy_rules ?? [];
  const joins = preview?.proposals?.relationships ?? [];
  const suggestedTable = preview?.suggested_target_table;
  const [table, setTable] = useState(
    scope.target_table ||
      (tables.includes(suggestedTable) ? suggestedTable : ""),
  );
  const changingTable = tables.includes(table) && table !== scope.target_table;
  const [repairs, setRepairs] = useState({});
  const [mappingIndexes, setMappingIndexes] = useState(() =>
    mappings.map((_, index) => index),
  );
  const [stableIndexes, setStableIndexes] = useState(() =>
    mappings.flatMap(({ mapping }, index) =>
      mapping.is_stable_id ? [index] : [],
    ),
  );
  const [ruleIds, setRuleIds] = useState([]);
  const [joinIndexes, setJoinIndexes] = useState([]);
  const [testedAgent, setTestedAgent] = useState(true);
  const [personalDataModes, setPersonalDataModes] = useState(() =>
    Object.fromEntries(
      mappings
        .filter(({ mapping }) => mapping.personal_data)
        .map(({ source_id }) => [
          source_id,
          defaultPersonalDataMode || "synthetic_replace",
        ]),
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
  const repairBody = changingTable
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
  const canRepair = changingTable || Boolean(repairBody.schema_repairs?.length);
  const unmappedRecords = sources.filter(
    ({ pin }) =>
      pin?.role === "seed_records" &&
      !mappingIndexes.some(
        (index) => mappings[index]?.source_id === pin.source_id,
      ),
  );
  const missingIdentity = sources.filter(
    ({ pin }) =>
      pin.role === "seed_records" &&
      mappings.some((m) => m.source_id === pin.source_id) &&
      !mappingIndexes.some(
        (index) =>
          mappings[index].source_id === pin.source_id &&
          stableIndexes.includes(index),
      ),
  );
  const validJoin = ({ from_source_id, from_field, to_source_id, to_field }) =>
    mappingIndexes.some(
      (index) =>
        mappings[index].source_id === from_source_id &&
        mappings[index].mapping.target_field === from_field,
    ) &&
    stableIndexes.some(
      (index) =>
        mappings[index].source_id === to_source_id &&
        mappings[index].mapping.target_field === to_field,
    );
  const canAccept =
    state === "ready" &&
    Boolean(digest && scope?.schema_digest) &&
    !unmappedRecords.length &&
    !missingIdentity.length &&
    !mappingIndexes.some(
      (index) =>
        mappings[index].mapping.personal_data &&
        personalDataModes[mappings[index].source_id] !== "synthetic_replace",
    ) &&
    !changingTable;

  const doRepair = (body) =>
    reprepare.mutate({
      ...pins,
      ...body,
      idempotency_key: keyFor(body),
    });
  const doAccept = () => {
    const body = {
      expected_draft_digest: digest,
      expected_schema_digest: scope.schema_digest,
      decisions: buildSourceReview(preview, {
        acceptedMappingIndexes: mappingIndexes,
        stableMappingIndexes: stableIndexes,
        acceptedRuleIds: ruleIds,
        acceptedRelationshipIndexes: joinIndexes.filter((index) =>
          validJoin(joins[index].relationship),
        ),
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
            helperText={
              suggestedTable && tables.includes(suggestedTable)
                ? `Suggested table: ${suggestedTable}`
                : undefined
            }
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
        {sources.map((source) => {
          const { pin, filename, fields = [], row_count: rows } = source;
          const sid = pin.source_id;
          const choices = (
            preview?.destination_schema?.repair_choices || []
          ).filter((c) => c.source_id === sid);
          const mode = personalDataModes[sid];
          const privacyPages = [
            ...new Set(
              diagnostics
                .filter(
                  (d) =>
                    d.code === "source_privacy_unresolved" &&
                    d.span?.source_id === sid,
                )
                .map((d) => d.span.page)
                .filter(Boolean),
            ),
          ];

          const selectedPersonal = mappings.filter(
            (m, i) =>
              m.source_id === sid &&
              m.mapping.personal_data &&
              mappingIndexes.includes(i),
          );
          return (
            <Stack key={sid} spacing={1}>
              <Typography fontWeight={600}>
                {filename || roles[pin.role]}
              </Typography>
              <Typography color="text.secondary">
                {roles[pin.role] || pin.role}
                {pin.role === "seed_records" &&
                  rows != null &&
                  ` · ${rows} rows`}
                {pin.role === "seed_records" &&
                  scope.target_table &&
                  ` · Table: ${scope.target_table}`}
              </Typography>
              {pin.role === "seed_records" && (
                <ReviewedRecords source={source} />
              )}
              {state === "needs_repair" && privateText.has(sid) && (
                <Stack spacing={0.5}>
                  <Alert severity="warning">
                    An email address or phone number was detected
                    {privacyPages.length
                      ? ` on ${privacyPages.length === 1 ? "page" : "pages"} ${privacyPages.join(", ")}`
                      : ""}
                    . Remove or replace those details in the review copy, or
                    upload a sanitized file.
                  </Alert>
                  <FormControlLabel
                    label="Select emails and phone numbers in this review copy"
                    control={
                      <Checkbox
                        checked={redactions.includes(sid)}
                        onChange={(e) =>
                          setRedactions(
                            toggled(redactions, sid, e.target.checked),
                          )
                        }
                      />
                    }
                  />
                </Stack>
              )}
              {state === "needs_repair" &&
                inferRepairs &&
                !tables.length &&
                fields.length > 0 && (
                  <SourceRepairs
                    source={source}
                    choices={choices}
                    repair={repairs[sid] || {}}
                    change={(key, value) => changeRepair(sid, key, value)}
                    editable={editable}
                  />
                )}
              {fields.length > 0 && (
                <ReviewedColumns
                  source={source}
                  mappings={mappings}
                  selected={mappingIndexes}
                  stable={stableIndexes}
                  onStable={
                    state === "ready"
                      ? (i, on) =>
                          setStableIndexes(toggled(stableIndexes, i, on))
                      : undefined
                  }
                  joins={joins}
                  onInclude={
                    state === "ready"
                      ? (i, on) =>
                          setMappingIndexes(toggled(mappingIndexes, i, on))
                      : undefined
                  }
                />
              )}
              {state === "ready" && mode && (
                <Stack>
                  <Typography variant="subtitle2">Personal data</Typography>
                  <Typography variant="body2">
                    Fields marked as personal must be replaced or excluded.
                  </Typography>
                  <PersonalDataChoice
                    compact
                    choice={mode === "synthetic_replace" ? "replace" : "none"}
                    availableChoices={[
                      "replace",
                      ...(selectedPersonal.length ? [] : ["none"]),
                    ]}
                    onChoose={(choice) =>
                      setPersonalDataModes({
                        ...personalDataModes,
                        [sid]:
                          choice === "replace" ? "synthetic_replace" : "none",
                      })
                    }
                  />
                </Stack>
              )}
              <ReviewedStatements
                source={source}
                rules={rules}
                ruleIds={ruleIds}
                onSelect={
                  state === "ready"
                    ? (id, on) => setRuleIds(toggled(ruleIds, id, on))
                    : undefined
                }
              />
            </Stack>
          );
        })}
        {state === "ready" && (
          <>
            {missingIdentity.map(({ pin, filename }) => (
              <Alert key={pin.source_id} severity="warning">
                Choose a stable ID for {filename}. Select a mapped column with a
                unique value on every row. If no column qualifies, correct the
                file or choose another destination.
              </Alert>
            ))}
            {unmappedRecords.map(({ pin, filename }) => (
              <Alert key={pin.source_id} severity="warning">
                {mappings.some(({ source_id }) => source_id === pin.source_id)
                  ? `Select a field mapping for ${filename} to include its records.`
                  : `No fields in ${filename} match the destination table. ${tables.length > 1 ? "Choose another table above." : "Choose a file with matching columns."}`}
              </Alert>
            ))}
            {joins.map(({ relationship }, index) => (
              <FormControlLabel
                key={index}
                label={`${readableField(relationship.from_field)} → ${readableField(relationship.to_field)}`}
                control={
                  <Checkbox
                    disabled={!validJoin(relationship)}
                    checked={
                      validJoin(relationship) && joinIndexes.includes(index)
                    }
                    onChange={(event) =>
                      setJoinIndexes(
                        toggled(joinIndexes, index, event.target.checked),
                      )
                    }
                  />
                }
              />
            ))}
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
      {Array.from(
        new Set(
          diagnostics
            .filter((item) => {
              if (item.code === "source_schema_ambiguous" && tables.length)
                return false;
              if (
                item.code === "source_schema_needs_repair" &&
                preview?.destination_schema?.repair_choices?.some(
                  (c) => c.source_id === item.span?.source_id,
                )
              )
                return false;
              if (
                item.code === "source_privacy_unresolved" &&
                sources.some(
                  (s) =>
                    s.pin.source_id === item.span?.source_id &&
                    s.pin.role !== "seed_records",
                )
              )
                return false;
              return true;
            })
            .map((item) => {
              const name = sources.find(
                (s) => s.pin.source_id === item.span?.source_id,
              )?.filename;
              const message =
                diagnosticMessages[item.code] ||
                item.message ||
                "Review the file and prepare it again.";
              return name ? `${name} · ${message}` : message;
            }),
        ),
      ).map((message) => (
        <Alert
          key={message}
          severity={state === "needs_repair" ? "warning" : "info"}
        >
          {message}
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
          {state === "needs_repair" && privateText.size > 0 && (
            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                disabled={!editable || !redactions.length}
                onClick={() => doRepair({ synthetic_text_sources: redactions })}
              >
                Replace with synthetic contacts
              </Button>
              <Button
                disabled={!editable || !redactions.length}
                onClick={() => doRepair({ redact_text_sources: redactions })}
              >
                Remove personal details and prepare again
              </Button>
            </Stack>
          )}
          {((state === "needs_repair" && hasRepairControls) ||
            (state === "ready" && changingTable)) && (
            <Button
              variant="contained"
              disabled={!editable || !canRepair}
              onClick={() => doRepair(repairBody)}
            >
              {changingTable || tables.length > 0
                ? "Prepare for this table"
                : "Prepare again"}
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
  defaultPersonalDataMode: PropTypes.oneOf(["synthetic_replace", "none"]),
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
  if (!review || !query.isFetchedAfterMount)
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
      {review.state === "accepted" && review.scenario_generation ? (
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
  defaultPersonalDataMode: PropTypes.oneOf(["synthetic_replace", "none"]),
  acceptLabel: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  onAccepted: PropTypes.func,
  onCancelled: PropTypes.func,
  onDismiss: PropTypes.func,
};
