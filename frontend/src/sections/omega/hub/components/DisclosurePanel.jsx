import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { DISCLOSURE_PARTS } from "../lib/disclosure";

import DisclosureText from "./DisclosureText";
import HubPanel from "./HubPanel";

// The confirmed label reads in the status green of the theme, falling back
// to the base palette where a screen renders without the app's theme.
const confirmedColor = (theme) =>
  theme.palette.status?.success?.fg || theme.palette.success.dark;

// "Disclosure, confirmed in review" (SPEC §6.7, §7.2): only the text the
// approving maintainer confirmed, high in the entry's side column so a
// visitor reads what it touches before running it. A part left empty reads
// "Not stated.". The publisher's draft is never shown.
export default function DisclosurePanel({
  disclosure,
  title = "Disclosure",
  subtitle = "Confirmed in review",
}) {
  if (!disclosure) return null;
  return (
    <HubPanel
      title={title}
      testId="hub-disclosure"
      dense
      action={
        <Box
          component="span"
          data-testid="hub-disclosure-confirmed"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            px: 0.875,
            borderRadius: 0.75,
            border: "1px solid",
            borderColor: confirmedColor,
            color: confirmedColor,
            typography: "s3",
            fontWeight: 600,
            lineHeight: "18px",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {subtitle}
        </Box>
      }
    >
      <Typography
        typography="s1_2"
        color="text.primary"
        data-testid="hub-disclosure-summary"
      >
        {disclosure.summary}
      </Typography>
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "grid",
          gridTemplateColumns: "96px minmax(0, 1fr)",
          rowGap: 1,
          columnGap: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          pt: 1.25,
        }}
      >
        {DISCLOSURE_PARTS.map((part) => (
          <React.Fragment key={part.key}>
            <Typography
              component="dt"
              typography="s2"
              color="text.secondary"
            >
              {part.label}
            </Typography>
            <Typography
              component="dd"
              typography="s2"
              color="text.primary"
              sx={{ m: 0, overflowWrap: "anywhere" }}
            >
              <DisclosureText
                text={disclosure[part.key] || "Not stated."}
                label={part.label}
              />
            </Typography>
          </React.Fragment>
        ))}
      </Box>
    </HubPanel>
  );
}

DisclosurePanel.propTypes = {
  disclosure: PropTypes.object,
  title: PropTypes.string,
  subtitle: PropTypes.string,
};
