import PropTypes from "prop-types";
import { Fragment } from "react";
import { Box } from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import { MISSING_VALUE, recipeSentenceParts } from "./metricPresentation";

// Inline text only: these render inside the caller's Typography, so the
// sentence wraps and reads as one line of copy.

/** A comparison value not chosen yet: "…" on screen, words for a screen
 * reader, which would otherwise read the ellipsis or nothing. */
export function MissingValue() {
  return (
    <>
      <span aria-hidden="true">{MISSING_VALUE}</span>
      <Box component="span" sx={visuallyHidden}>
        no value yet
      </Box>
    </>
  );
}

/** One condition, e.g. "resolved (latest) is 1", from conditionParts. */
export function ConditionText({ parts }) {
  if (!parts.operator) return parts.subject;
  return (
    <>
      {parts.subject} {parts.operator}{" "}
      {parts.missing ? <MissingValue /> : parts.value}
    </>
  );
}
ConditionText.propTypes = {
  parts: PropTypes.shape({
    subject: PropTypes.string.isRequired,
    operator: PropTypes.string,
    value: PropTypes.string,
    missing: PropTypes.bool,
  }).isRequired,
};

/** The recipe sentence, with each missing value marked for assistive tech. */
export default function RecipeSentence({ recipe, labelFor }) {
  const { lead, clauses } = recipeSentenceParts(recipe, labelFor);
  return (
    <>
      {lead}
      {clauses.map((parts, index) => (
        <Fragment key={index}>
          {index ? " and " : " where "}
          <ConditionText parts={parts} />
        </Fragment>
      ))}
    </>
  );
}
RecipeSentence.propTypes = {
  recipe: PropTypes.object.isRequired,
  labelFor: PropTypes.func,
};
