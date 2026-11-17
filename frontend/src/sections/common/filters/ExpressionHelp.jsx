import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { FUNCTIONS } from "./expressionParser";

// W5-1 · the rules of the language, word for word (W5-SCOPE §5 A0).
const EXPRESSION_RULES = [
  'A comparison with a missing value is false. `is empty` is true for missing, "", [] and {}.',
  "= and != on text are exact; contains and startswith ignore case.",
  "A pass/fail eval compared with a verdict word compares its verdict.",
  "split and tags are lists.",
  "An unprefixed run field = any candidate when 2+ runs are in view (as the pills' any_candidate), else the only run. any. = at least one run; all. = every run; baseline. = the baseline.",
  "At most one any / all / unprefixed run field per comparison, else the error 'Use one of any / all per comparison (or name the runs).'",
  "Run-vs-run comparisons and arithmetic are allowed.",
];

const EXPRESSION_SYNTAX = [
  "and · or · not · ( )",
  "= != > >= < <=",
  "in (…) · not in (…) · contains · not contains · startswith · is empty · is not empty",
  "+ - * /",
  `${Object.keys(FUNCTIONS).join(" · ")}`,
  "\"text\" or 'text' · numbers · true · false · null",
  "pass · fail · skipped · na · error",
  '#2B.field · #2B·2.field · baseline.field · any.field · all.field · run("label or id").field · `name with spaces`',
];

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** The help panel under the expression input. */
const ExpressionHelp = ({ dense = false }) => (
  <Stack spacing={1} data-testid="expression-help" sx={{ typography: "s2" }}>
    <Box>
      <Typography
        typography="s2"
        fontWeight="fontWeightSemiBold"
        sx={{ mb: 0.5 }}
      >
        Syntax
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {EXPRESSION_SYNTAX.map((line) => (
          <Box
            component="li"
            key={line}
            sx={{ fontFamily: MONO, fontSize: 12, lineHeight: 1.7 }}
          >
            {line}
          </Box>
        ))}
      </Box>
    </Box>
    <Box>
      <Typography
        typography="s2"
        fontWeight="fontWeightSemiBold"
        sx={{ mb: 0.5 }}
      >
        Rules
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {EXPRESSION_RULES.slice(0, dense ? 4 : EXPRESSION_RULES.length).map(
          (line) => (
            <Box component="li" key={line} sx={{ lineHeight: 1.6 }}>
              <Typography typography="s2" color="text.secondary">
                {line}
              </Typography>
            </Box>
          ),
        )}
      </Box>
    </Box>
  </Stack>
);

ExpressionHelp.propTypes = { dense: PropTypes.bool };

export default ExpressionHelp;
