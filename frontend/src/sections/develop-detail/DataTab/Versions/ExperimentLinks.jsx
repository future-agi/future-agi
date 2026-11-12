import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

/**
 * `Used by #4 #5 #6`: the experiments that ran on a version, each a link to
 * its experiment page (PRODUCT-SPEC §3.5 History line 4). Experiments pin a
 * version id, so these never move when a tag moves.
 */
const ExperimentLinks = ({ experiments, label = "Used by" }) => {
  if (!experiments?.length) return null;
  return (
    <Stack direction="row" alignItems="center" gap={0.5} flexWrap="wrap">
      <Typography typography="s2" color="text.secondary" sx={{ mr: 0.25 }}>
        {label}
      </Typography>
      {experiments.map((e) => (
        <CustomTooltip
          key={e.id}
          show
          title={`#${e.ordinal}${e.name ? ` · ${e.name}` : ""} · open experiment`}
          placement="bottom"
          arrow
          size="small"
        >
          <Box
            component={RouterLink}
            to={`/dashboard/develop/experiment/${e.id}/data`}
            onClick={(ev) => ev.stopPropagation()}
            sx={{
              typography: "s3",
              fontWeight: 500,
              px: 0.75,
              lineHeight: "20px",
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              color: "text.primary",
              textDecoration: "none",
              fontVariantNumeric: "tabular-nums",
              "&:hover": { borderColor: "primary.main", color: "primary.main" },
            }}
          >
            #{e.ordinal}
          </Box>
        </CustomTooltip>
      ))}
    </Stack>
  );
};

ExperimentLinks.propTypes = {
  experiments: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      ordinal: PropTypes.number,
      name: PropTypes.string,
    }),
  ),
  label: PropTypes.string,
};

export default ExperimentLinks;
