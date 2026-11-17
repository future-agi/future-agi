import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Tab, Tabs } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import PageHeader from "src/components/page-header";
import SvgColor from "src/components/svg-color";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";

// One header for the evals list, usage and groups pages, with links between
// them, so each page can reach the other two.
const EVALS_PAGE_DESCRIPTION =
  "Create, test and reuse evals that score responses for accuracy, safety, tone and more.";

const EVALS_DOCS_URL =
  "https://docs.futureagi.com/docs/cookbook/quickstart/first-eval";

const EVALS_PAGES = [
  { value: "evaluators", label: "Evals", to: "/dashboard/evaluations" },
  { value: "usage", label: "Usage", to: "/dashboard/evaluations/usage" },
  { value: "groups", label: "Groups", to: "/dashboard/evaluations/groups" },
];

export default function EvalsPageHeader({ currentTab }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <PageHeader
        title="Evaluations"
        description={EVALS_PAGE_DESCRIPTION}
        actions={
          <Button
            variant="outlined"
            size="small"
            sx={{ color: "text.primary", borderColor: "divider" }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href={EVALS_DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            View docs
          </Button>
        }
        sx={{ mb: 0 }}
      />
      <Box component="nav" aria-label="Evaluation pages">
        <Tabs
          textColor="primary"
          value={currentTab}
          sx={{
            boxShadow: "none",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {EVALS_PAGES.map((page) => {
            const isCurrent = page.value === currentTab;
            return (
              <Tab
                key={page.value}
                value={page.value}
                label={page.label}
                component={RouterLink}
                to={page.to}
                aria-current={isCurrent ? "page" : undefined}
                onClick={
                  page.value === "usage" && !isCurrent
                    ? () =>
                        trackEvent(Events.evalsUsagetabClicked, {
                          [PropertyName.click]: true,
                        })
                    : undefined
                }
                sx={{
                  paddingLeft: "24px",
                  paddingRight: "24px",
                  ["&:not(:last-of-type)"]: {
                    marginRight: "4px",
                  },
                }}
              />
            );
          })}
        </Tabs>
      </Box>
    </Box>
  );
}

EvalsPageHeader.propTypes = {
  currentTab: PropTypes.oneOf(EVALS_PAGES.map((page) => page.value)).isRequired,
};
