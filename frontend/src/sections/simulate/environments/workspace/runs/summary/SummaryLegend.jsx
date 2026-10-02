import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const INLINE_COUNT = 3;

// One eval in the legend; hovering it highlights its line in the graph.
function LegendItem({ item, onHighlight }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.625}
      onMouseEnter={() => onHighlight(item.id)}
      onMouseLeave={() => onHighlight(null)}
      sx={{
        px: 0.5,
        py: 0.25,
        borderRadius: 0.5,
        minWidth: 0,
        cursor: "default",
      }}
    >
      <Box
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          bgcolor: item.color,
        }}
      />
      <Typography
        noWrap
        sx={{ typography: "s3", maxWidth: 200, color: "text.secondary" }}
      >
        {item.name}
      </Typography>
    </Stack>
  );
}
LegendItem.propTypes = {
  item: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    color: PropTypes.string,
  }).isRequired,
  onHighlight: PropTypes.func.isRequired,
};

// The graph legend: the first few shown evals inline, the rest behind a
// "+N more" chip whose hover lists them. Which evals are shown is the eval
// picker's job; the legend only names them and highlights on hover.
export default function SummaryLegend({ evals, onHighlight }) {
  const inline = evals.slice(0, INLINE_COUNT);
  const folded = evals.slice(INLINE_COUNT);

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ flex: 1, minWidth: 0, justifyContent: "flex-end" }}
    >
      {inline.map((item) => (
        <LegendItem key={item.id} item={item} onHighlight={onHighlight} />
      ))}
      {folded.length > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="bottom-end"
          title={
            // Capped and scrollable, so a long eval set stays on screen.
            <Stack
              data-legend-overflow
              spacing={0.25}
              alignItems="flex-start"
              sx={{
                maxHeight: 240,
                overflowY: "auto",
                pr: 0.5,
                scrollbarWidth: "thin",
                scrollbarColor: (t) => `${t.palette.divider} transparent`,
              }}
            >
              {folded.map((item) => (
                <LegendItem
                  key={item.id}
                  item={item}
                  onHighlight={onHighlight}
                />
              ))}
            </Stack>
          }
        >
          <Box
            tabIndex={0}
            sx={{
              flexShrink: 0,
              px: 1,
              py: 0.25,
              borderRadius: 0.75,
              cursor: "default",
              border: "1px solid",
              borderColor: "divider",
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            +{folded.length} more
          </Box>
        </CustomTooltip>
      )}
    </Stack>
  );
}

SummaryLegend.propTypes = {
  evals: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      color: PropTypes.string,
    }),
  ).isRequired,
  onHighlight: PropTypes.func.isRequired,
};
