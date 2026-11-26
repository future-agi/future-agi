import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import {
  useEnvironmentSourceReview,
  useEnvironmentSourceGeneration,
} from "src/api/simulate-environments/sourceImports";
import { ReviewedStatements, ReviewedRecords } from "./ReviewedSource";
import { readableField } from "./importSourceReview";
import SideDrawer from "../../components/SideDrawer";

const tabs = {
  read: "What was read",
  mapping: "Mapping",
  personal: "Personal data",
  used: "Used by",
};

export default function SourceDrawer({ envId, source, initialTab, onClose }) {
  const [tab, setTab] = useState(
    Object.hasOwn(tabs, initialTab) ? initialTab : "read",
  );
  const query = useEnvironmentSourceReview(source.review_id);
  const review =
    query.data?.scope?.id === (source.review_owner_id || envId) &&
    query.data?.state === "accepted"
      ? query.data
      : null;
  const file = review?.preview?.sources?.find(
    (item) => item.pin?.source_id === source.source_id,
  );
  const mappings = (review?.preview?.proposals?.field_mappings || []).filter(
    (item) => item.source_id === source.source_id,
  );
  const displayMappings =
    file?.accepted_mappings ?? mappings.map((item) => item.mapping);
  const personal = mappings.filter((item) => item.mapping.personal_data);
  const hasUsage = Array.isArray(source.used_by);
  const readGeneration = review?.scope?.kind === "environment" && !hasUsage;
  const usageTab = tab === "used" && hasUsage;
  const generation = useEnvironmentSourceGeneration(
    readGeneration ? review.id : null,
    readGeneration ? review.scenario_generation : undefined,
  );
  const scenarios =
    source.used_by ??
    (generation.data?.generated_scenarios || []).filter((item) =>
      item.citations?.some((c) => c.source_id === source.source_id),
    );
  return (
    <SideDrawer open onClose={onClose} width={560}>
      <Box
        component="header"
        sx={{ px: 2, py: 1.5, pr: 5, borderBottom: 1, borderColor: "divider" }}
      >
        <Typography variant="h6">{source.filename || "Source file"}</Typography>
        <Typography color="text.secondary">
          {source.unsaved
            ? "In the environment draft"
            : source.unsaved === false
              ? "Saved in this environment"
              : ""}
        </Typography>
      </Box>
      <Tabs
        value={tab}
        onChange={(_, value) => setTab(value)}
        aria-label="Source details"
      >
        {Object.entries(tabs)
          .filter(
            ([key]) =>
              (key !== "personal" || personal.length) &&
              (key !== "used" ||
                hasUsage ||
                review?.scope?.kind === "environment"),
          )
          .map(([key, label]) => (
            <Tab key={key} value={key} label={label} />
          ))}
      </Tabs>
      <Stack spacing={1.25} role="tabpanel" sx={{ p: 2 }}>
        {query.isError && !usageTab ? (
          <Alert
            severity="error"
            action={<Button onClick={query.refetch}>Retry</Button>}
          >
            Could not open this source review.
          </Alert>
        ) : query.isLoading && !usageTab ? (
          <Typography>Opening source review…</Typography>
        ) : !file && !usageTab ? (
          <Typography>
            No review details are available for this source.
          </Typography>
        ) : tab === "read" ? (
          <>
            {file.pin.role === "seed_records" && file.row_count != null && (
              <Typography>{file.row_count} rows read</Typography>
            )}
            {file.fields?.map((field) => (
              <Typography key={field.source_field}>
                {readableField(field.source_field)}
                {field.inferred_type && " · " + field.inferred_type}
              </Typography>
            ))}
            <ReviewedRecords source={file} />
            <ReviewedStatements
              source={file}
              rules={review.preview?.proposals?.policy_rules || []}
              ruleIds={[]}
            />
            {file.origin?.kind !== "knowledge_base" &&
              !file.fields?.length &&
              !file.statements?.some((statement) => statement.text) &&
              !(file.pin.role === "seed_records" && file.row_count != null) && (
                <Typography>
                  No excerpt was recorded for this source.
                </Typography>
              )}
          </>
        ) : tab === "mapping" ? (
          <>
            {displayMappings.length ? (
              <Typography variant="subtitle2">
                {file.accepted_mappings
                  ? "Accepted field mappings"
                  : "Proposed field mappings"}
              </Typography>
            ) : (
              <Typography>No field mappings for this source.</Typography>
            )}
            {displayMappings.map((mapping) => (
              <Typography key={mapping.source_field}>
                {readableField(mapping.source_field)} →{" "}
                {readableField(mapping.target_field) || "Unmapped"}
                {mapping.data_type && " · " + mapping.data_type}
                {mapping.is_stable_id && " · Stable identifier"}
                {mapping.personal_data ? " · Personal data" : ""}
              </Typography>
            ))}
          </>
        ) : tab === "personal" ? (
          <>
            <Typography variant="subtitle2">
              Fields flagged during review
            </Typography>
            {personal.map(({ mapping }) => (
              <Typography key={mapping.source_field}>
                {readableField(mapping.source_field)}
              </Typography>
            ))}
          </>
        ) : (
          <>
            {generation.isError ? (
              <Alert
                severity="error"
                action={<Button onClick={generation.refetch}>Retry</Button>}
              >
                Could not load scenario citations.
              </Alert>
            ) : scenarios.length ? (
              scenarios.map((scenario) => (
                <Box key={scenario.scenario_key}>
                  <Typography>{scenario.name}</Typography>
                  {scenario.citations
                    .filter((c) => c.source_id === source.source_id)
                    .map((citation, i) => (
                      <Typography key={i} variant="caption" display="block">
                        {[
                          citation.page != null && "Page " + citation.page,
                          citation.row != null && "Row " + citation.row,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </Typography>
                    ))}
                </Box>
              ))
            ) : (
              <Typography>
                {["queued", "running"].includes(generation.data?.state)
                  ? "Scenario generation is in progress."
                  : "No generated scenarios with a citation to this source."}
              </Typography>
            )}
          </>
        )}
      </Stack>
    </SideDrawer>
  );
}
SourceDrawer.propTypes = {
  envId: PropTypes.string.isRequired,
  source: PropTypes.object.isRequired,
  initialTab: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};
