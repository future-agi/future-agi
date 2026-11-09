// HO D1 · a field's translation, shown right under its card in the Row view
// so the original text stays in sight (the same TranslationBlock the
// section's Translate uses; the translate read gives its header line).
import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useTranslateInfo } from "src/api/develop/row-collab";
import TranslationBlock from "./TranslationBlock";

export default function FieldTranslation({
  datasetId,
  rowId,
  result,
  onClose,
}) {
  // The field's ⋯ menu made the same read (one cache key).
  const info = useTranslateInfo(datasetId, rowId);
  if (!result) return null;
  return (
    <Box data-testid="field-translation" sx={{ mt: -0.25, mb: 1 }}>
      <TranslationBlock result={result} info={info.data} onClose={onClose} />
    </Box>
  );
}

FieldTranslation.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  result: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
