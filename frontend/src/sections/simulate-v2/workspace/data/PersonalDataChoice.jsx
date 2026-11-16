import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PERSONAL_CHOICES, WHO_SEES_PERSONAL } from "../../_mock/dataMapping";

/**
 * The personal-data choice for one source: keep it, replace it with synthetic
 * values, or say there is none. The builder's suggestion is marked, never
 * applied for the person, and the line below says who sees the result.
 */
export default function PersonalDataChoice({
  suggested = "replace",
  choice,
  columns = [],
  onChoose,
  readOnly = false,
  compact = false,
}) {
  /* Compact (review drawer): the three choices on one line, each one's
     meaning on hover. */
  if (compact)
    return (
      <Stack
        direction="row"
        role="radiogroup"
        aria-label="Personal data"
        spacing={1.5}
        useFlexGap
        flexWrap="wrap"
      >
        {PERSONAL_CHOICES.map((c) => {
          const on = choice === c.id;
          return (
            <Tooltip key={c.id} title={c.note} arrow>
              <ButtonBase
                role="radio"
                aria-checked={on}
                disabled={readOnly}
                onClick={() => onChoose(c.id)}
                sx={{ gap: 0.5, typography: "s3", py: 0.25 }}
              >
                <Iconify
                  icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                  width={14}
                />
                <Box component="span" sx={{ fontWeight: on ? 600 : 400 }}>
                  {c.id === "replace" ? "Replace" : c.label}
                  {c.id === suggested ? " (suggested)" : ""}
                </Box>
              </ButtonBase>
            </Tooltip>
          );
        })}
      </Stack>
    );
  return (
    <Box>
      <Stack role="radiogroup" aria-label="Personal data" spacing={0.25}>
        {PERSONAL_CHOICES.map((c) => {
          const on = choice === c.id;
          return (
            <ButtonBase
              key={c.id}
              role="radio"
              aria-checked={on}
              disabled={readOnly}
              onClick={() => onChoose(c.id)}
              sx={{
                justifyContent: "flex-start",
                alignItems: "flex-start",
                gap: 0.75,
                py: 0.5,
                textAlign: "left",
                width: "100%",
              }}
            >
              <Iconify
                icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                width={14}
                sx={{ mt: "3px", flexShrink: 0 }}
              />
              <Box>
                <Typography
                  sx={{ typography: "s2", fontWeight: on ? 600 : 400 }}
                >
                  {c.label}
                  {c.id === suggested && (
                    <Box
                      component="span"
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontWeight: 400,
                        ml: 0.75,
                      }}
                    >
                      Suggested
                    </Box>
                  )}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {c.note}
                </Typography>
              </Box>
            </ButtonBase>
          );
        })}
      </Stack>
      {columns.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          {`The builder found: ${columns.join(", ")}.`}
        </Typography>
      )}
      <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
        {WHO_SEES_PERSONAL}
      </Typography>
    </Box>
  );
}

PersonalDataChoice.propTypes = {
  suggested: PropTypes.string,
  choice: PropTypes.string,
  columns: PropTypes.arrayOf(PropTypes.string),
  onChoose: PropTypes.func.isRequired,
  readOnly: PropTypes.bool,
  compact: PropTypes.bool,
};
