import React, { useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  Stack,
  Typography,
} from "@mui/material";
import { hasAlignmentRoutes, useAgreement } from "./alignmentApi";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { sourceLine } from "./agreementModel";
import SourceReport from "./SourceReport";
import DisagreementsTable from "./DisagreementsTable";
import ComparePanel from "./ComparePanel";
import ConfidencePanel from "./ConfidencePanel";
import ImproveJudgePanel from "./ImproveJudgePanel";
import TrainJudgePanel from "./TrainJudgePanel";
import CheckedVersions from "./CheckedVersions";
import StoredAgreement from "./StoredAgreement";

/**
 * Evals › <eval> › Agreement (W7-6, W7-1): every label source this eval's
 * verdicts can be checked against (dataset labels, judge reviews, run
 * feedback), one source's agreement report, the disagreements with the
 * reviewers' notes, Compare with another judge, decision confidence and
 * Improve judge. `?dataset=&label=` (Dataset Summary › Improve this judge)
 * opens that source. Compare, Improve and Train keep their own state and
 * queries: a compare never re-reads (or unmounts) the report around them,
 * and a loaded tab is never swapped for a spinner.
 */
const AdvancedAgreementTab = ({ templateId, evalData }) => {
  const [searchParams] = useSearchParams();
  const [sourceKey, setSourceKey] = useState(
    () => searchParams.get("source") || null,
  );
  const params = {
    source: sourceKey,
    dataset_id: sourceKey ? null : searchParams.get("dataset"),
    label_id: sourceKey ? null : searchParams.get("label"),
    include: "pairs",
  };
  const { data, isError, refetch } = useAgreement(templateId, params, {
    keepPrevious: true,
  });
  if (!hasAlignmentRoutes()) return null;
  if (!data && !isError)
    return (
      <Box sx={{ p: 4, display: "flex", justifyContent: "center" }}>
        <CircularProgress size={24} />
      </Box>
    );
  if (!data)
    return (
      <Typography
        typography="s2"
        color="error.main"
        sx={{ p: 3, cursor: "pointer" }}
        onClick={() => refetch()}
      >
        Couldn&apos;t load agreement · Try again
      </Typography>
    );
  const sources = data?.sources ?? [];
  const report = data?.report ?? null;
  const name = evalData?.name ?? "";
  // Decision confidence: only where a decision model judged this eval's
  // cells on some run (the eval's own judge, or a run's judge override).
  const showConfidence = Boolean(
    report?.judge_is_decision || data?.decision_runs > 0,
  );
  return (
    <Stack
      direction={{ xs: "column", lg: "row" }}
      gap={3}
      sx={{ p: 2 }}
      data-testid="w7-agreement-tab"
    >
      <Box sx={{ width: { lg: 340 }, flex: "0 0 auto" }}>
        <Typography typography="s1" fontWeight={600} sx={{ mb: 1 }}>
          Human labels
        </Typography>
        {sources.length ? (
          <List dense disablePadding data-testid="w7-sources">
            {sources.map((s) => (
              <ListItemButton
                key={s.key}
                selected={s.key === report?.source_key}
                onClick={() => setSourceKey(s.key)}
                sx={{ borderRadius: 1, mb: 0.5 }}
              >
                <Typography typography="s2" data-testid="w7-source-line">
                  {sourceLine(s)}
                </Typography>
              </ListItemButton>
            ))}
          </List>
        ) : (
          <Typography typography="s2" color="text.secondary">
            No row of {name || "this eval"} has a reviewer&apos;s label yet.
            Label rows in a dataset review queue, or give feedback on a
            run&apos;s scores, and they show here.
          </Typography>
        )}
        <Box sx={{ mt: 2 }}>
          <CheckedVersions templateId={templateId} />
        </Box>
      </Box>
      {report || showConfidence ? (
        <Stack gap={3} sx={{ flex: 1, minWidth: 0 }}>
          {report ? (
            <SourceReport report={report} pairs={data?.pairs ?? []} />
          ) : null}
          {report && !report.numeric ? (
            <>
              <Divider />
              <DisagreementsTable
                source={report.source}
                items={report.disagreements}
              />
              <Divider />
              <ComparePanel
                key={`compare-${report.source_key}`}
                templateId={templateId}
                report={report}
              />
              <ImproveJudgePanel
                key={report.source_key}
                templateId={templateId}
                templateName={name}
                mode={data?.improve_mode ?? null}
                editableCopy={data?.editable_copy ?? null}
                report={report}
              />
              <Divider />
              <TrainJudgePanel
                key={`train-${report.source_key}`}
                templateId={templateId}
                templateName={name}
                mode={data?.improve_mode ?? null}
                editableCopy={data?.editable_copy ?? null}
                report={report}
              />
            </>
          ) : null}
          {showConfidence ? (
            <>
              {report ? <Divider /> : null}
              <ConfidencePanel templateId={templateId} />
            </>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
};

const AgreementTab = (props) =>
  isSurfaceShown("evals.agreementTools") ? (
    <AdvancedAgreementTab {...props} />
  ) : (
    <StoredAgreement key={props.templateId} templateId={props.templateId} />
  );

Object.defineProperty(AgreementTab, "ready", {
  get: () => hasAlignmentRoutes() && isSurfaceShown("evals.agreement"),
});

AgreementTab.propTypes = {
  templateId: PropTypes.string,
  evalData: PropTypes.object,
};
AdvancedAgreementTab.propTypes = AgreementTab.propTypes;

export default AgreementTab;
