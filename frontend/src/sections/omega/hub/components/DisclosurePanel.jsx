import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { DISCLOSURE_PARTS } from "../lib/disclosure";

import DisclosureText, { DisclosureWords } from "./DisclosureText";
import HubPanel from "./HubPanel";

// The confirmed label reads in the status green of the theme, falling back
// to the base palette where a screen renders without the app's theme.
const confirmedColor = (theme) =>
  theme.palette.status?.success?.fg || theme.palette.success.dark;

// Text in the panel wraps at spaces and at the break points a name carries,
// never inside a word.
const WRAP = { overflowWrap: "normal", wordBreak: "normal" };

// "Disclosure, confirmed in review" (SPEC §6.7, §7.2): only the text the
// approving maintainer confirmed, high in the entry's side column so a
// visitor reads what it touches before running it. Each part's label sits
// above its text, so the text has the card's whole width; names in it are
// set in the code face. A part left empty reads "Not stated.". The
// publisher's draft is never shown.
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
        sx={WRAP}
      >
        <DisclosureWords text={disclosure.summary} codeTypography="code_md" />
      </Typography>
      <Box
        component="dl"
        sx={{
          m: 0,
          display: "flex",
          flexDirection: "column",
          gap: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
          pt: 1.25,
        }}
      >
        {DISCLOSURE_PARTS.map((part) => (
          <Box
            key={part.key}
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 0.25,
              minWidth: 0,
            }}
          >
            <Typography
              component="dt"
              typography="s2"
              color="text.secondary"
              fontWeight="fontWeightMedium"
            >
              {part.label}
            </Typography>
            <Typography
              component="dd"
              typography="s2"
              color="text.primary"
              sx={{ m: 0, ...WRAP }}
            >
              <DisclosureText
                text={disclosure[part.key] || "Not stated."}
                label={part.label}
              />
            </Typography>
          </Box>
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
