import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ANSWER_TYPES,
  MAX_CATEGORIES,
  MAX_DEFINITION_LENGTH,
  MAX_QUESTION_LENGTH,
} from "./instantEvalPayloads";

// Each category can carry a definition: Jev's rule for when to pick it.
function CategoryEditor({ categories, definitions, onChange }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const value = draft.trim();
    if (!value || categories.length >= MAX_CATEGORIES) return;
    if (categories.some((c) => c.toLowerCase() === value.toLowerCase())) {
      setDraft("");
      return;
    }
    onChange({ categories: [...categories, value], definitions });
    setDraft("");
  };
  const remove = (index) => {
    const label = categories[index];
    const rest = { ...definitions };
    delete rest[label];
    onChange({
      categories: categories.filter((_, i) => i !== index),
      definitions: rest,
    });
  };
  return (
    <Stack spacing={0.75}>
      {categories.map((category, index) => (
        <Stack
          key={`${category}-${index}`}
          direction="row"
          alignItems="center"
          spacing={1}
        >
          <Chip
            label={category}
            size="small"
            title={category}
            sx={{ maxWidth: 160, flexShrink: 0 }}
          />
          <TextField
            size="small"
            fullWidth
            value={definitions?.[category] || ""}
            placeholder="When should Jev pick this? (optional)"
            onChange={(event) =>
              onChange({
                categories,
                definitions: { ...definitions, [category]: event.target.value },
              })
            }
            inputProps={{
              maxLength: MAX_DEFINITION_LENGTH,
              "aria-label": `Definition of ${category}`,
            }}
            sx={{ "& .MuiInputBase-input": { py: 0.5, fontSize: 13 } }}
          />
          <Tooltip title={`Remove ${category}`}>
            <IconButton
              size="small"
              onClick={() => remove(index)}
              aria-label={`Remove ${category}`}
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Tooltip>
        </Stack>
      ))}
      <TextField
        size="small"
        fullWidth
        placeholder={
          categories.length >= MAX_CATEGORIES
            ? `Up to ${MAX_CATEGORIES} categories`
            : "Type a category and press Enter"
        }
        value={draft}
        disabled={categories.length >= MAX_CATEGORIES}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            add();
          }
        }}
        onBlur={add}
        inputProps={{ "aria-label": "Add category", maxLength: 200 }}
      />
    </Stack>
  );
}

CategoryEditor.propTypes = {
  categories: PropTypes.arrayOf(PropTypes.string).isRequired,
  definitions: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

// A toggle shows no keyboard focus of its own: ring it like the size cards.
const answerTypeSx = {
  px: 1.5,
  py: 0.25,
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: 1,
  },
};

export default function QuestionEditor({
  index,
  value,
  error,
  onChange,
  onRemove,
  autoFocus,
  placeholder,
}) {
  const update = (patch) => onChange({ ...value, ...patch });
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: error ? "error.main" : "divider",
        borderRadius: 1,
        p: 1.5,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 1 }}
      >
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Question {index + 1}
        </Typography>
        {onRemove && (
          <Tooltip title="Remove question">
            <IconButton
              size="small"
              onClick={onRemove}
              aria-label={`Remove question ${index + 1}`}
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
      <TextField
        multiline
        minRows={2}
        fullWidth
        autoFocus={autoFocus}
        value={value.question}
        onChange={(event) => update({ question: event.target.value })}
        placeholder={
          placeholder ||
          "Did the agent promise a refund without checking the order first?"
        }
        error={Boolean(error)}
        helperText={error || " "}
        inputProps={{
          maxLength: MAX_QUESTION_LENGTH,
          "aria-label": `Question ${index + 1}`,
        }}
      />
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={value.answerType}
          onChange={(_, answerType) => answerType && update({ answerType })}
          aria-label={`Answer type for question ${index + 1}`}
        >
          <ToggleButton value={ANSWER_TYPES.YES_NO} sx={answerTypeSx}>
            Yes / No
          </ToggleButton>
          <ToggleButton value={ANSWER_TYPES.CATEGORY} sx={answerTypeSx}>
            Categories
          </ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {value.answerType === ANSWER_TYPES.CATEGORY && (
        <Box sx={{ mt: 1.25 }}>
          <CategoryEditor
            categories={value.categories || []}
            definitions={value.definitions || {}}
            onChange={update}
          />
        </Box>
      )}
    </Box>
  );
}

QuestionEditor.propTypes = {
  index: PropTypes.number.isRequired,
  value: PropTypes.shape({
    question: PropTypes.string,
    answerType: PropTypes.string,
    categories: PropTypes.arrayOf(PropTypes.string),
    definitions: PropTypes.object,
  }).isRequired,
  error: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func,
  autoFocus: PropTypes.bool,
  placeholder: PropTypes.string,
};
