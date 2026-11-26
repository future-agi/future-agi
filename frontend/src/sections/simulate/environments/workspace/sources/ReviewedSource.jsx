import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { paths } from "src/routes/paths";
import { Columns } from "./MappingTab";
import ProposalItem from "./ProposalItem";
import { readableField } from "./importSourceReview";

function passageCaption(rule, text) {
  if (rule?.status === "conflict") return "Conflicting rule · reference only";
  if (rule?.status === "supported") return "Policy passage";
  const instruction =
    /\b(?:must|shall|should|required to|prohibited from|may only)\b|^(?:always|never|do not|escalate|require|verify|refuse|deny|approve|confirm|refund|route|offer|ask|provide|collect|check|include|exclude|be sure to)\b/i;
  return instruction.test(text.trim())
    ? "Policy guidance · scenario context"
    : "Reference passage";
}

export function ReviewedStatements({ source, rules, ruleIds, onSelect }) {
  if (source.origin?.kind === "knowledge_base") {
    const pages = new Map();
    for (const statement of source.statements || []) {
      pages.set(statement.page, (pages.get(statement.page) || 0) + 1);
    }
    const kb = paths.dashboard.knowledge_base;
    const href = source.origin.kb_id
      ? kb + "/" + encodeURIComponent(source.origin.kb_id)
      : kb;
    return (
      <Stack spacing={0.5}>
        {[...pages.entries()].map(([page, count]) => (
          <Typography variant="body2" key={page ?? "unpaginated"}>
            {page != null && "Page " + page + " · "}
            {count} {count === 1 ? "passage" : "passages"}
          </Typography>
        ))}
        <Link href={href} target="_blank" rel="noopener noreferrer">
          Read the original in Knowledge Base
        </Link>
      </Stack>
    );
  }
  return (source.statements || [])
    .filter((statement) => statement.text)
    .map((statement) => {
      const rule = rules.find(
        (candidate) => candidate.statement_id === statement.statement_id,
      );
      const ref = [
        source.filename,
        statement.page != null && `page ${statement.page}`,
      ]
        .filter(Boolean)
        .join(", ");
      // Old previews omitted the flag and may end exactly at the excerpt limit.
      const excerpt = statement.text_truncated ?? statement.text.length >= 2000;
      if (!rule || rule.status !== "supported" || !onSelect || excerpt)
        return (
          <Stack spacing={0.25} key={statement.statement_id}>
            <Typography
              component="blockquote"
              sx={{
                m: 0,
                pl: 1.5,
                borderLeft: "2px solid",
                borderColor: "divider",
                whiteSpace: "pre-wrap",
              }}
            >
              {statement.text}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {ref} ·{" "}
              {excerpt
                ? "Excerpt · full passage kept as reference only"
                : passageCaption(rule, statement.text)}
            </Typography>
          </Stack>
        );
      return (
        <ProposalItem
          key={rule.rule_id}
          inset={0}
          item={{
            id: rule.rule_id,
            title: statement.text,
            rule: statement.text,
            kind: "rule",
            cite: { ref },
          }}
          checked={ruleIds.includes(statement.statement_id)}
          onToggle={(event) =>
            onSelect(statement.statement_id, event.target.checked)
          }
        />
      );
    });
}
ReviewedStatements.propTypes = {
  source: PropTypes.object.isRequired,
  rules: PropTypes.array.isRequired,
  ruleIds: PropTypes.array.isRequired,
  onSelect: PropTypes.func,
};

export function ReviewedRecords({ source }) {
  if (source.origin?.kind === "knowledge_base") return null;
  const { sample_rows: samples = [], rejected_rows: rejected = [] } = source;
  return (
    <>
      {!!samples.length && Object.keys(samples[0]).length > 0 && (
        <Box sx={{ overflowX: "auto" }}>
          <Typography variant="subtitle2">Sample rows</Typography>
          <Box
            component="table"
            sx={{
              borderSpacing: 1,
              "& th, & td": {
                px: 1,
                textAlign: "left",
                typography: "caption",
              },
            }}
          >
            <thead>
              <tr>
                {Object.keys(samples[0]).map((k) => (
                  <th key={k}>{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {samples.map((row, i) => (
                <tr key={i}>
                  {Object.keys(samples[0]).map((k) => (
                    <td key={k}>{row[k] ?? "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Box>
        </Box>
      )}
      {!!rejected.length && (
        <Alert severity="warning">
          {source.rejected_count ?? rejected.length} rejected rows
          {source.rejected_count > rejected.length
            ? " (showing the first 100)"
            : ""}
          .
          {source.rejected_row_details?.length ? (
            <Box component="ul" sx={{ my: 0, pl: 2 }}>
              {source.rejected_row_details.map(({ row, reasons }) => (
                <li key={row}>
                  Row {row}: {reasons.join(" ") || "The row could not be read."}
                </li>
              ))}
            </Box>
          ) : (
            ` Row numbers: ${rejected.join(", ")}.`
          )}
        </Alert>
      )}
    </>
  );
}
ReviewedRecords.propTypes = { source: PropTypes.object.isRequired };

export function ReviewedColumns({
  source,
  mappings,
  joins,
  selected,
  onInclude,
  stable,
  onStable,
}) {
  const columns = source.fields.map((field) => {
    const index = mappings.findIndex(
      (candidate) =>
        candidate.source_id === source.pin.source_id &&
        candidate.mapping.source_field === field.source_field,
    );
    const mapping = mappings[index]?.mapping;
    const target = readableField(
      mapping?.target_field || field.suggested_target_field,
    );
    const related = joins.filter(
      ({ relationship: r }) =>
        r.from_source_id === source.pin.source_id &&
        r.from_field === field.source_field,
    );
    return {
      name: field.source_field,
      index,
      type: mapping?.data_type || field.inferred_type || "unknown",
      stableId: stable
        ? stable.includes(index)
        : Boolean(mapping?.is_stable_id || field.suggested_stable_id),
      canIdentify:
        index >= 0 &&
        selected.includes(index) &&
        field.null_count === 0 &&
        field.unique_count === source.row_count &&
        source.row_count > 0,
      ref:
        related
          .map(({ relationship: r }) => readableField(r.to_field))
          .join(", ") ||
        (target && target !== field.source_field ? target : null),
      mappable:
        index >= 0 && !(stable ? stable.includes(index) : mapping.is_stable_id),
      included: selected.includes(index),
    };
  });
  return (
    <Columns
      source={{ ui: { parsed: { columns } } }}
      targets={[]}
      readOnly
      onInclude={onInclude}
      onStable={onStable}
    />
  );
}
ReviewedColumns.propTypes = {
  source: PropTypes.object.isRequired,
  mappings: PropTypes.array.isRequired,
  joins: PropTypes.array.isRequired,
  selected: PropTypes.array.isRequired,
  onInclude: PropTypes.func,
  stable: PropTypes.array,
  onStable: PropTypes.func,
};

export function SourceRepairs({ source, choices, repair, change, editable }) {
  const fieldNames = source.fields.map((f) => f.source_field);
  const text = choices
    .filter((c) => c.decision === "keep_text")
    .flatMap((c) => c.fields);
  const empty = text.filter((name) =>
    source.fields.some(
      (f) => f.source_field === name && f.null_count === source.row_count,
    ),
  );
  const identity = choices.find((c) => c.decision === "choose_identity");
  const suggested = source.fields.find(
    (f) =>
      /(^id$|_id$)/i.test(f.source_field) &&
      f.null_count === 0 &&
      f.unique_count === source.row_count,
  );
  return (
    <Stack spacing={1}>
      {empty.length > 0 && (
        <Alert severity="info">
          {empty.length} empty {empty.length === 1 ? "column" : "columns"}:{" "}
          {empty.join(", ")}. Confirm to store as text.
        </Alert>
      )}
      {[
        ["text_fields", "Treat as text", text.length ? text : fieldNames],
        [
          "identity_fields",
          "Identify records by",
          identity?.fields || fieldNames,
        ],
      ].map(([key, title, options]) => {
        if (
          choices.length &&
          (key === "text_fields" ? !text.length : !identity)
        )
          return null;
        return (
          <Stack key={key} spacing={0.5}>
            {identity && key === "identity_fields" && (
              <Typography variant="body2">
                Choose a unique column or combination to identify each row.
                {suggested && ` Suggested: ${suggested.source_field}.`}
              </Typography>
            )}
            <TextField
              select
              size="small"
              label={title}
              disabled={!editable}
              SelectProps={{ multiple: true }}
              value={repair[key] || []}
              onChange={(e) => change(key, e.target.value)}
            >
              {options.map((name) => (
                <MenuItem key={name} value={name}>
                  {name}
                </MenuItem>
              ))}
            </TextField>
            {key === "text_fields" && text.length > 1 && (
              <Button size="small" onClick={() => change(key, text)}>
                Treat all {text.length} columns as text
              </Button>
            )}
          </Stack>
        );
      })}
      {[
        [
          "separate_entity",
          "confirm_entity",
          "Use a separate table for this source",
        ],
        [
          "manual_relationships",
          "review_relationships",
          "Review relationships manually",
        ],
      ]
        .filter(([, decision]) => choices.some((c) => c.decision === decision))
        .map(([key, , title]) => (
          <FormControlLabel
            key={key}
            label={title}
            control={
              <Checkbox
                checked={Boolean(repair[key])}
                onChange={(e) => change(key, e.target.checked)}
              />
            }
          />
        ))}
    </Stack>
  );
}
SourceRepairs.propTypes = {
  source: PropTypes.object.isRequired,
  choices: PropTypes.array.isRequired,
  repair: PropTypes.object.isRequired,
  change: PropTypes.func.isRequired,
  editable: PropTypes.bool,
};
