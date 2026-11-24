import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";

const plural = (count, word) => `${word}${count === 1 ? "" : "s"}`;

export default function StateSummary({ env, envState, counts, onGo }) {
  const scenarioCount = counts?.scenarios ?? (envState?.scenarios?.length || 0);
  const evalCount = counts?.evaluations ?? (envState?.evals?.length || 0);
  const runCount = counts?.runs ?? (envState?.runs || []).length;
  const ruleCount = counts?.hardRules ?? (env?.rules?.length || 0);

  const items = [
    {
      id: "scenarios",
      value: scenarioCount,
      word: "scenario",
      to: "scenarios",
    },
    { id: "evals", value: evalCount, word: "evaluation", to: "evals" },
    { id: "runs", value: runCount, word: "run", to: "runs" },
    { id: "rules", value: ruleCount, word: "rule", to: "contract" },
  ].filter((item) => item.value > 0);

  if (!items.length) return null;

  return (
    <Stack
      direction="row"
      alignItems="center"
      flexWrap="wrap"
      sx={{ mb: 2.5, typography: "s1", color: "text.tertiary" }}
    >
      {items.map((item, index) => (
        <Box key={item.id} component="span" sx={{ display: "inline-flex" }}>
          {index > 0 && (
            <Box component="span" sx={{ mx: 1, color: "text.disabled" }}>
              ·
            </Box>
          )}
          <Box
            component="button"
            type="button"
            onClick={() => onGo?.(item.to)}
            sx={{
              p: 0,
              border: 0,
              bgcolor: "transparent",
              font: "inherit",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
              cursor: "pointer",
              "&:hover": { color: "text.primary", textDecoration: "underline" },
            }}
          >
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: 500 }}
            >
              {item.value}
            </Box>
            {` ${plural(item.value, item.word)}`}
          </Box>
        </Box>
      ))}
    </Stack>
  );
}

StateSummary.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  counts: PropTypes.shape({
    scenarios: PropTypes.number,
    evaluations: PropTypes.number,
    runs: PropTypes.number,
    hardRules: PropTypes.number,
  }),
  onGo: PropTypes.func,
};
