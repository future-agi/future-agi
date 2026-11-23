import PropTypes from "prop-types";
import { IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY } from "./designReview.constants";

export default function ToolChip({ tool, source, onRemove }) {
  return (
    <Tooltip
      arrow
      title={
        [tool.description, source].filter(Boolean).join(" · ") || tool.name
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.25}
        sx={{
          pl: 0.875,
          pr: onRemove ? 0.25 : 0.875,
          py: 0.125,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.75,
          "& .chip-x": { opacity: 0.5 },
          "&:hover .chip-x, &:focus-within .chip-x": { opacity: 1 },
        }}
      >
        <Typography component="span" sx={{ typography: "s3" }}>
          {tool.name}
        </Typography>
        {onRemove && (
          <IconButton
            className="chip-x"
            size="small"
            aria-label={REVIEW_COPY.removeNamed(tool.name)}
            onClick={onRemove}
            sx={{ p: 0.25, color: "text.tertiary" }}
          >
            <Iconify icon="solar:close-linear" width={12} />
          </IconButton>
        )}
      </Stack>
    </Tooltip>
  );
}

ToolChip.propTypes = {
  tool: PropTypes.shape({
    name: PropTypes.string.isRequired,
    description: PropTypes.string,
  }).isRequired,
  source: PropTypes.string,
  onRemove: PropTypes.func,
};
