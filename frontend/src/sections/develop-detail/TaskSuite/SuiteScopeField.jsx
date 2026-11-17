import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from "@mui/material";
import { checkSentence } from "./checkTypes";
import { APPLIES_TO } from "./suiteChecksModel";

/**
 * W6 C1: the check editor's "Applies to" (This task · Every task in the
 * suite) and the suite's checks, each with Edit and Remove.
 */
const SuiteScopeField = ({
  scope,
  onScope,
  suite,
  editingKey,
  onEdit,
  onRemove,
  lockedToTask = false,
}) => (
  <Stack gap={0.75} data-testid="check-applies-to">
    <Typography typography="s2" fontWeight={600}>
      Applies to
    </Typography>
    <RadioGroup
      row
      value={scope}
      onChange={(e) => onScope(e.target.value)}
      aria-label="Applies to"
    >
      {APPLIES_TO.map((o) => (
        <FormControlLabel
          key={o.id}
          value={o.id}
          disabled={lockedToTask && o.id === "suite"}
          control={<Radio size="small" />}
          label={<Typography typography="s2">{o.label}</Typography>}
          data-testid={`check-applies-${o.id}`}
        />
      ))}
    </RadioGroup>
    {scope === "suite" && (
      <Typography typography="s3" color="text.secondary">
        Judged on every task of this suite next to its own checks, in every run
        started after you save the version; kept with the suite&apos;s settings.
      </Typography>
    )}
    {suite.length > 0 && (
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 1.25,
          py: 0.75,
        }}
        data-testid="suite-checks"
      >
        <Typography typography="s3" color="text.secondary">
          {suite.length} suite-wide {suite.length === 1 ? "check" : "checks"}
        </Typography>
        {suite.map((c) => (
          <Stack
            key={c.key}
            direction="row"
            alignItems="center"
            gap={1}
            data-testid={`suite-check-${c.key}`}
          >
            <Typography
              typography="s2"
              sx={{ flex: 1, minWidth: 0 }}
              noWrap
              title={checkSentence(c)}
            >
              <b>{c.name ?? c.key}</b> · {checkSentence(c)}
            </Typography>
            <Button
              size="small"
              variant="text"
              disabled={c.key === editingKey}
              onClick={() => onEdit(c)}
              sx={{ typography: "s3", minWidth: 0 }}
            >
              Edit
            </Button>
            <Button
              size="small"
              variant="text"
              color="error"
              onClick={() => onRemove(c.key)}
              sx={{ typography: "s3", minWidth: 0 }}
              data-testid={`suite-check-remove-${c.key}`}
            >
              Remove
            </Button>
          </Stack>
        ))}
      </Box>
    )}
  </Stack>
);

SuiteScopeField.propTypes = {
  scope: PropTypes.oneOf(["task", "suite"]).isRequired,
  onScope: PropTypes.func.isRequired,
  suite: PropTypes.array.isRequired,
  editingKey: PropTypes.string,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  lockedToTask: PropTypes.bool,
};

export default SuiteScopeField;
