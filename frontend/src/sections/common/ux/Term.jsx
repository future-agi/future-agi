import React from "react";
import PropTypes from "prop-types";
import { Link, Typography } from "@mui/material";
import { glossaryEntry } from "./glossary";
import HoverCard from "./oneview/HoverCard";

/**
 * Term — jargon with an explanation one hover away (R3 G0 §B3; one view
 * G-05). A HoverCard with glossary content: 1 px dotted underline
 * (`text.disabled`, `text.secondary` on hover), `cursor: help`; opens on
 * hover (300 ms), keyboard focus or click (click pins it); `Esc` closes and
 * returns focus. Shows the glossary `short` (+ `long`, + `Learn more ↗`).
 *
 * Props (frozen):
 * @param {string} props.k  GLOSSARY key (`passAt1`, `baseline`, …)
 * @param {React.ReactNode} [props.children]  the visible word (default: the
 *   entry label)
 * @param {string} [props.extra]  one more sentence for this context
 *   (`#2B is the pinned baseline.`)
 * @param {object} [props.sx]
 */
export default function Term({ k, children, extra, sx }) {
  const entry = glossaryEntry(k);
  if (!entry) return <>{children}</>;
  const more = [extra, entry.long].filter(Boolean).join(" ");
  return (
    <HoverCard
      trigger={children ?? entry.label}
      title={entry.label}
      triggerProps={{ "data-term": k }}
      cardProps={{ "data-term-popper": k }}
      triggerSx={{
        cursor: "help",
        textDecorationLine: "underline",
        textDecorationStyle: "dotted",
        textDecorationThickness: "1px",
        textUnderlineOffset: "3px",
        textDecorationColor: (theme) => theme.palette.text.disabled,
        "&:hover, &[aria-expanded='true']": {
          textDecorationColor: (theme) => theme.palette.text.secondary,
        },
        ...sx,
      }}
      actions={
        entry.docs ? (
          <Link
            href={entry.docs}
            target="_blank"
            rel="noopener noreferrer"
            typography="s2"
            underline="hover"
          >
            Learn more ↗
          </Link>
        ) : null
      }
    >
      <Typography typography="s2" color="text.secondary">
        {entry.short}
      </Typography>
      {more && (
        <Typography typography="s2" color="text.secondary" sx={{ mt: 0.75 }}>
          {more}
        </Typography>
      )}
    </HoverCard>
  );
}

Term.propTypes = {
  k: PropTypes.string.isRequired,
  children: PropTypes.node,
  extra: PropTypes.string,
  sx: PropTypes.object,
};
