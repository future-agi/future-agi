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
import { agreementReportForView, sourceLine } from "./agreementModel";
import SourceReport from "./SourceReport";
import DisagreementsTable from "./DisagreementsTable";
import ImproveJudgePanel from "./ImproveJudgePanel";
import CheckedVersions from "./CheckedVersions";
import StoredAgreement from "./StoredAgreement";

/**
 * Evals › <eval> › Agreement (W7-6, W7-1): every label source this eval's
 * verdicts can be checked against (dataset labels, judge reviews, run
 * feedback), one source's agreement report, the disagreements with the
 * reviewers' notes and Improve judge. `?dataset=&label=` (Dataset Summary
 * › Improve this judge) opens that source. The served contract supports
 * improvement only; other tools remain named with an availability line,
 * not mounted as controls that would send unsupported requests.
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
  const report = agreementReportForView(data?.report ?? null);
  const name = evalData?.name ?? "";
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
      {report ? (
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
              <ImproveJudgePanel
                key={report.source_key}
                templateId={templateId}
                templateName={name}
                mode={data?.improvement?.mode ?? data?.improve_mode ?? null}
                editableCopy={data?.editable_copy ?? null}
                report={data?.improvement?.judge_model ? {
                  ...report,
                  judge_model: data.improvement.judge_model,
                } : report}
              />
              <Typography typography="s3" color="text.secondary">
                Compare, training and confidence reports are not served by this endpoint.
              </Typography>
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
