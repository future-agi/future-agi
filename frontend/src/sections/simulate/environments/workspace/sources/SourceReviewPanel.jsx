import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Checkbox,
  CircularProgress,
  Collapse,
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
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import {
  buildSourceReview,
  checkableRuleIds,
  readableField,
  reviewItems,
} from "./importSourceReview";
import PersonalDataChoice from "./PersonalDataChoice";
import ProposalItem from "./ProposalItem";
import { ProposalGroupHeading } from "./ProposalsDrawer";
import { groupProposals } from "./proposals";
import {
  ReviewedColumns,
  ReviewedRecords,
  SourceRepairs,
} from "./ReviewedSource";
import SourceScenarioGeneration from "./SourceScenarioGeneration";

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
// What each personal data choice sends: a records mode, or a text reprepare field.
const RECORD_MODES = {
  keep: "keep",
  replace: "synthetic_replace",
  none: "none",
};
const TEXT_REPAIRS = {
  keep: "keep_text_sources",
  replace: "synthetic_text_sources",
  remove: "redact_text_sources",
};
const TEXT_NOTES = {
  keep: "The details stay in the text as they are.",
  replace: "Emails and phone numbers are swapped for made-up ones.",
};
const toggled = (items, value, checked) =>
  checked ? [...items, value] : items.filter((item) => item !== value);

function ShowMore({ label, children }) {
  const [open, setOpen] = useState(false);
  const timeout = usePrefersReducedMotion() ? 0 : "auto";
  return (
    <>
      {/* A span, so a read-only review's disabled fieldset doesn't disable reading. */}
      <ButtonBase
        component="span"
        role="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        sx={{
          alignSelf: "flex-start",
          typography: "s3",
          color: "primary.main",
        }}
      >
        {label}
      </ButtonBase>
      <Collapse in={open} timeout={timeout} unmountOnExit>
        {children}
      </Collapse>
    </>
  );
}
ShowMore.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function SourceReviewEditor({
  review,
  canEdit,
  acceptLabel,
  onAccepted,
  onCancelled,
  defaultPersonalDataMode,
  compact,
  readingNames = [],
  onRepair,
  stale,
  actionsEl,
  inlineActions,
  onShown,
}) {
  const { id, state, revision, draft_digest: digest, preview, scope } = review;
  // A page with its own header shows Cancel and Approve there, not at the foot.
  const withActions = (node) =>
    actionsEl ? createPortal(node, actionsEl) : node;
  const accept = useAcceptSourceReview(onAccepted);
  const cancel = useCancelSourceReview(onCancelled);
  const reprepare = useReprepareEnvironmentSourceReview();
  const timeout = usePrefersReducedMotion() ? 0 : "auto";
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
  const hasRepairControls =
    tables.length > 0 ||
    (inferRepairs && sources.some((item) => item.fields?.length));
  const mappings = preview?.proposals?.field_mappings ?? [];
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
  // An accepted review shows what was accepted; an open one starts with every
  // checkable rule ticked; a cancelled or expired one adds nothing.
  const [ruleIds, setRuleIds] = useState(() => {
    if (state === "accepted")
      return sources.flatMap((source) => source.accepted_rule_ids || []);
    return unfinished.includes(state) ? checkableRuleIds(preview) : [];
  });
  const [joinIndexes, setJoinIndexes] = useState([]);
  // A text choice is one-way, so a pick is only applied on Apply.
  const [textChoices, setTextChoices] = useState({});
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
  const changeCount =
    sources.length +
    ruleIds.length +
    joinIndexes.filter((index) => validJoin(joins[index].relationship)).length;
  const canAccept =
    state === "ready" &&
    Boolean(digest && scope?.schema_digest) &&
    !unmappedRecords.length &&
    !missingIdentity.length &&
    !mappingIndexes.some(
      (index) =>
        mappings[index].mapping.personal_data &&
        !["keep", "synthetic_replace"].includes(
          personalDataModes[mappings[index].source_id],
        ),
    ) &&
    !changingTable;

  const doRepair = (body) => {
    reprepare
      .mutateAsync({
        ...pins,
        ...body,
        idempotency_key: keyFor(body),
      })
      .then((result) => {
        const fixed = (body.schema_repairs || []).flatMap((repair) => {
          const remaining =
            result.preview?.destination_schema?.repair_choices || [];
          const pendingText = remaining
            .filter(
              (c) =>
                c.source_id === repair.source_id && c.decision === "keep_text",
            )
            .flatMap((c) => c.fields);
          return (repair.text_fields || []).filter(
            (field) => !pendingText.includes(field),
          );
        });
        onRepair(
          fixed.length
            ? `Stored ${fixed.length} ${fixed.length === 1 ? "column" : "columns"} as text: ${fixed.join(", ")}.`
            : "",
        );
      })
      .catch(() => {}); // The mutation error is rendered in the review.
  };
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

  const renderRecords = (source) => {
    const { pin, filename, fields = [], row_count: rows } = source;
    const sid = pin.source_id;
    const choices = (preview?.destination_schema?.repair_choices || []).filter(
      (c) => c.source_id === sid,
    );
    const needsIdentity = missingIdentity.some(
      (item) => item.pin.source_id === sid,
    );
    const columns = fields.length > 0 && (
      <ReviewedColumns
        source={source}
        mappings={mappings}
        selected={mappingIndexes}
        stable={stableIndexes}
        onStable={
          state === "ready"
            ? (i, on) => setStableIndexes(toggled(stableIndexes, i, on))
            : undefined
        }
        joins={joins}
        onInclude={
          state === "ready"
            ? (i, on) => setMappingIndexes(toggled(mappingIndexes, i, on))
            : undefined
        }
      />
    );
    return (
      <Stack key={sid} spacing={1} sx={{ pb: 1.5 }}>
        <Stack
          direction={compact ? "row" : "column"}
          alignItems={compact ? "baseline" : undefined}
          spacing={compact ? 1 : 0}
        >
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {filename}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Structured scenario data
            {rows != null &&
              ` · ${rows} rows · ${source.field_count ?? fields.length} columns`}
            {scope.target_table && ` · Table: ${scope.target_table}`}
          </Typography>
        </Stack>
        {!compact && <ReviewedRecords source={source} />}
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
        {(!compact || needsIdentity) && columns}
        {compact && fields.length > 0 && (
          <ShowMore label="Show records">
            {/* A long file scrolls inside its own box. */}
            <Stack
              spacing={1}
              sx={{ mt: 1, maxHeight: 360, overflowY: "auto", pr: 1 }}
            >
              <ReviewedRecords source={source} />
              {!needsIdentity && columns}
            </Stack>
          </ShowMore>
        )}
      </Stack>
    );
  };
  const renderPersonal = ({ id: itemId, sourceId: sid, title }) => {
    const mode = personalDataModes[sid];
    const selectedPersonal = mappings.filter(
      (m, i) =>
        m.source_id === sid &&
        m.mapping.personal_data &&
        mappingIndexes.includes(i),
    );
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
    return (
      <Stack key={itemId} spacing={0.5} sx={{ pb: 1.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 500 }}>
          {title}
        </Typography>
        {privateText.has(sid) ? (
          <>
            <Alert severity="warning">
              An email address or phone number was detected
              {privacyPages.length
                ? ` on ${privacyPages.length === 1 ? "page" : "pages"} ${privacyPages.join(", ")}`
                : ""}
              . Keep, replace or remove those details, or upload a sanitized
              file. Once applied, the choice can&apos;t be changed in this
              review.
            </Alert>
            <PersonalDataChoice
              compact
              vertical
              choice={textChoices[sid]}
              notes={TEXT_NOTES}
              availableChoices={Object.keys(TEXT_REPAIRS)}
              readOnly={!editable}
              onChoose={(choice) =>
                setTextChoices({ ...textChoices, [sid]: choice })
              }
            />
            <Button
              size="small"
              variant="outlined"
              disabled={!editable || !textChoices[sid]}
              onClick={() =>
                doRepair({ [TEXT_REPAIRS[textChoices[sid]]]: [sid] })
              }
              sx={{ alignSelf: "flex-start" }}
            >
              Apply
            </Button>
          </>
        ) : (
          <>
            <Typography variant="body2">
              Fields marked as personal must be kept, replaced or excluded.
            </Typography>
            <PersonalDataChoice
              compact
              choice={Object.keys(RECORD_MODES).find(
                (choice) => RECORD_MODES[choice] === mode,
              )}
              availableChoices={[
                "keep",
                "replace",
                ...(selectedPersonal.length ? [] : ["none"]),
              ]}
              onChoose={(choice) =>
                setPersonalDataModes({
                  ...personalDataModes,
                  [sid]: RECORD_MODES[choice],
                })
              }
            />
          </>
        )}
      </Stack>
    );
  };
  const renderItem = (item) => {
    if (item.kind === "table") return renderRecords(item.source);
    if (item.kind === "personal") return renderPersonal(item);
    return (
      <ProposalItem
        key={item.id}
        item={item}
        inset={0}
        foldText={compact}
        // A repair remounts the review, so ticks before it are only decided once it's ready.
        hideCheckbox={["reading", "needs_repair"].includes(state)}
        checked={ruleIds.includes(item.id)}
        onToggle={(event) =>
          setRuleIds(toggled(ruleIds, item.id, event.target.checked))
        }
      />
    );
  };
  // The text repair shows while the review waits on it; the records choice once mappings are ready.
  const items = reviewItems(preview, {
    testedAgent: state === "ready" ? testedAgent : undefined,
  }).filter(
    (item) =>
      item.kind !== "personal" ||
      (privateText.has(item.sourceId)
        ? state === "needs_repair"
        : state === "ready" && Boolean(personalDataModes[item.sourceId])),
  );
  // Records files take the records group's place; their column controls carry the decision.
  const groups = groupProposals([
    ...items,
    ...sources
      .filter(({ pin }) => pin?.role === "seed_records")
      .map((source) => ({ id: source.pin.source_id, kind: "table", source })),
  ]);

  return (
    <Stack spacing={2} sx={{ flex: 1 }}>
      {state === "reading" &&
        readingNames.length > 0 &&
        readingNames.map((name, index) => (
          <Typography key={index} role="status">
            Reading {name}…
          </Typography>
        ))}
      {state === "reading" && !readingNames.length && (
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
        {groups.length > 0 && (
          // onShown runs once the body has slid fully open, so its full height is there.
          <Collapse in appear timeout={timeout} onEntered={onShown}>
            {groups.map(({ group, items: inGroup }, index) => (
              <Box key={group}>
                <ProposalGroupHeading
                  group={group}
                  count={inGroup.length}
                  first={index === 0}
                  inset={0}
                />
                {inGroup.map(renderItem)}
              </Box>
            ))}
          </Collapse>
        )}
        {tables.length > 0 && (
          <TextField
            select
            size="small"
            label="Scenario data table"
            helperText={
              suggestedTable && tables.includes(suggestedTable)
                ? `Suggested table: ${suggestedTable}`
                : "Choose the table these rows go into."
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
            <Box
              component="details"
              sx={{
                "& summary": {
                  cursor: "pointer",
                  typography: "s3",
                  color: "text.secondary",
                },
              }}
            >
              <summary>Source visibility</summary>
              <FormControlLabel
                label="Make approved source facts visible to the tested agent"
                control={
                  <Switch
                    checked={testedAgent}
                    onChange={(event) => setTestedAgent(event.target.checked)}
                  />
                }
              />
            </Box>
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
      {unfinished.includes(state) &&
        withActions(
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={
              actionsEl
                ? undefined
                : {
                    py: 1.25,
                    borderTop: "1px solid",
                    borderColor: "divider",
                    justifyContent: "flex-end",
                    ...(!inlineActions && {
                      mt:
                        scope.kind === "environment"
                          ? "auto !important"
                          : undefined,
                      position: "sticky",
                      bottom: 0,
                      zIndex: 1,
                      bgcolor: "background.paper",
                    }),
                  }
            }
          >
            <Button
              variant="outlined"
              size="small"
              disabled={!canEdit || pending}
              onClick={() =>
                cancel.mutate({
                  ...pins,
                  expected_draft_digest: digest || null,
                })
              }
            >
              {scope.kind === "environment" ? "Reject" : "Cancel review"}
            </Button>
            {((state === "needs_repair" && hasRepairControls) ||
              (state === "ready" && changingTable)) && (
              <Button
                variant="contained"
                size="small"
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
                size="small"
                disabled={!editable || !canAccept}
                onClick={doAccept}
                startIcon={
                  accept.isPending ? (
                    <CircularProgress size={14} color="inherit" />
                  ) : undefined
                }
              >
                {accept.isPending
                  ? scope.kind === "environment"
                    ? "Adding…"
                    : "Approving…"
                  : scope.kind === "environment"
                    ? `Add ${changeCount} ${changeCount === 1 ? "change" : "changes"}`
                    : acceptLabel}
              </Button>
            )}
          </Stack>,
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
  readingNames: PropTypes.arrayOf(PropTypes.string),
  compact: PropTypes.bool,
  onRepair: PropTypes.func.isRequired,
  stale: PropTypes.bool,
  actionsEl: PropTypes.object,
  // In a chat card the footer follows the review instead of sticking to the scroll box.
  inlineActions: PropTypes.bool,
  // Called once the review's body has drawn in full.
  onShown: PropTypes.func,
  defaultPersonalDataMode: PropTypes.oneOf(Object.values(RECORD_MODES)),
};

export default function SourceReviewPanel({
  reviewId,
  ownerId,
  environmentBasis,
  onDismiss,
  ...editor
}) {
  const [repairNotice, setRepairNotice] = useState(null);
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
      {repairNotice?.id === reviewId && repairNotice.message && (
        <Alert severity="success" role="status">
          {repairNotice.message}
        </Alert>
      )}
      <SourceReviewEditor
        onRepair={(message) => setRepairNotice({ id: reviewId, message })}
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
  readingNames: PropTypes.arrayOf(PropTypes.string),
  compact: PropTypes.bool,
  defaultPersonalDataMode: PropTypes.oneOf(Object.values(RECORD_MODES)),
  acceptLabel: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  onAccepted: PropTypes.func,
  onCancelled: PropTypes.func,
  onDismiss: PropTypes.func,
  actionsEl: PropTypes.object,
  inlineActions: PropTypes.bool,
  onShown: PropTypes.func,
};
