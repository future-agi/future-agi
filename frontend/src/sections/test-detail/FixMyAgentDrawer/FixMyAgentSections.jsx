import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../components/show";
import FixableRecommendations from "./FixableRecommendations";
import NonFixableRecommendations from "./NonFixableRecommendations";

const FixMyAgentSections = ({ refetch, optimizerAnalysis }) => {
  const agentLevelFixableRecommendations =
    optimizerAnalysis?.response?.agent_level?.actionable_recommendations ?? [];
  const domainLevelFixableRecommendations =
    optimizerAnalysis?.response?.domain_level?.actionable_recommendations ?? [];
  const fixableActionableRecommendations = [
    ...(agentLevelFixableRecommendations ?? []),
    ...(domainLevelFixableRecommendations ?? []),
  ];
  const notFixableActionableRecommendations =
    optimizerAnalysis?.response?.system_level?.actionable_recommendations || [];

  const lastUpdated =
    optimizerAnalysis?.last_updated ?? optimizerAnalysis?.lastUpdated;
  return (
    <>
      <ShowComponent condition={!optimizerAnalysis?.response}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            height: "100%",
          }}
        >
          <Typography variant="s2_1" fontWeight="fontWeightMedium">
            There are no suggestions yet, click the refresh button to get
            suggestions
          </Typography>
        </Box>
      </ShowComponent>
      <ShowComponent condition={optimizerAnalysis?.response}>
        <Box sx={{ overflowY: "auto" }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1,
              flex: 1,
            }}
          >
            <FixableRecommendations
              domainLevelFixableRecommendations={
                domainLevelFixableRecommendations
              }
              agentLevelFixableRecommendations={
                agentLevelFixableRecommendations
              }
              summary={optimizerAnalysis?.response?.insights ?? null}
              refetch={refetch}
              lastUpdated={lastUpdated}
            />

            <NonFixableRecommendations
              humanComparisonSummary={
                optimizerAnalysis?.response?.system_level
                  ?.human_comparison_summary ?? ""
              }
              notFixableActionableRecommendations={
                notFixableActionableRecommendations
              }
            />
          </Box>
        </Box>
        <ShowComponent
          condition={
            !fixableActionableRecommendations?.length &&
            !notFixableActionableRecommendations?.length
          }
        >
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
            }}
          >
            <Typography
              variant="s2_1"
              fontWeight="fontWeightMedium"
              sx={{ textAlign: "center" }}
            >
              We don&apos;t have any recommended fixes for now. This may mean
              issues are rare, inconsistent, or below the current threshold.
              Consider adding more nuanced evaluations in your simulation tests.
            </Typography>
          </Box>
        </ShowComponent>
      </ShowComponent>
    </>
  );
};

FixMyAgentSections.propTypes = {
  refetch: PropTypes.func,
  optimizerAnalysis: PropTypes.object,
};

export default FixMyAgentSections;
