import React from "react";
import PropTypes from "prop-types";
import { Button, Typography } from "@mui/material";
import { creditWords } from "src/sections/projects/LLMTracing/InstantEval/page/askFilter";
import { ErrorText } from "./parts";

export default function ActionCreditNote({ query, label }) {
  if (query.isError || query.data?.unavailable)
    return (
      <Typography variant="caption" color="error.main">
        {query.data?.unavailable
          ? query.data.message
          : "The cost could not be checked."}{" "}
        <Button
          size="small"
          color="inherit"
          disabled={query.isFetching}
          onClick={() => query.refetch()}
        >
          Retry cost check
        </Button>
      </Typography>
    );
  if (!query.isSuccess || query.isFetching)
    return (
      <Typography variant="caption" color="text.secondary">
        Checking {label.toLowerCase()} cost…
      </Typography>
    );
  if (!query.data) return null;
  return (
    <Typography
      variant="caption"
      color={query.data.limited ? "error.main" : "text.secondary"}
    >
      {label}: {creditWords(query.data.estimate)} per action. Charged for actual
      usage.
      {query.data.limited && (
        <>
          {" "}
          <ErrorText message={query.data.message} billing />
        </>
      )}
    </Typography>
  );
}

ActionCreditNote.propTypes = {
  query: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
};
