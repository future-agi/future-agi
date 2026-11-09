import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { DISCLOSURE_PARTS } from "../lib/disclosure";

import { HubSection } from "./HubStates";
import DisclosureText from "./DisclosureText";

// "Disclosure, confirmed in review" (SPEC §6.7, §7.2): only the text the
// approving maintainer confirmed. The publisher's draft is never shown.
export default function DisclosurePanel({ disclosure, title = "Disclosure", subtitle = "Confirmed in review" }) {
  if (!disclosure) return null;
  return (
    <HubSection
      title={title}
      testId="hub-disclosure"
      action={
        <Typography typography="s3" color="success.dark" fontWeight={600} data-testid="hub-disclosure-confirmed">
          {subtitle}
        </Typography>
      }
    >
      <Typography typography="s2" color="text.primary" data-testid="hub-disclosure-summary">
        {disclosure.summary}
      </Typography>
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gridTemplateColumns: "96px minmax(0, 1fr)",
          rowGap: 0.75,
          columnGap: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          pt: 1.25,
        }}
      >
        {DISCLOSURE_PARTS.map((part) => (
          <React.Fragment key={part.key}>
            <Typography component="dt" typography="s2" color="text.secondary">
              {part.label}
            </Typography>
            <Typography component="dd" typography="s2" color="text.primary" sx={{ m: 0, overflowWrap: "anywhere" }}>
              <DisclosureText text={disclosure[part.key] || "—"} label={part.label} />
            </Typography>
          </React.Fragment>
        ))}
      </Box>
    </HubSection>
  );
}

DisclosurePanel.propTypes = {
  disclosure: PropTypes.object,
  title: PropTypes.string,
  subtitle: PropTypes.string,
};
