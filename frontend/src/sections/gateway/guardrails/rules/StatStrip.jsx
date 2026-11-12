import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { EMPTY } from "../../utils/formatters";

/**
 * A row of numbers in one bordered strip (the Vercel firewall overview
 * shape, on Future AGI tokens): label above, value below, cells split by
 * dividers. Two to a row on a phone, where an odd last cell grows to fill
 * its row. It never shrinks: the page frame's content is a flex column with
 * min-height 0, where a box that clips its overflow would otherwise collapse
 * when the page scrolls. Bottom borders are longhands so the phone-only
 * width never resets their colour to the text colour.
 */
export default function StatStrip({ label, items, loading, error }) {
  return (
    <Box
      role="group"
      aria-label={label}
      sx={{
        display: "flex",
        flexWrap: "wrap",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        backgroundColor: "background.paper",
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      {items.map((item) => (
        <Box
          key={item.label}
          sx={{
            flex: "1 1 0",
            minWidth: { xs: "50%", md: 0 },
            px: 2,
            py: 1.5,
            borderRight: "1px solid",
            borderBottomStyle: "solid",
            borderBottomWidth: { xs: "1px", md: 0 },
            borderColor: "divider",
            mr: "-1px",
            mb: { xs: "-1px", md: 0 },
          }}
        >
          <Typography typography="s2" color="text.secondary" noWrap>
            {item.label}
          </Typography>
          {loading ? (
            <Skeleton width={56} height={28} />
          ) : (
            <Typography typography="m2" color="text.primary" noWrap>
              {error ? EMPTY : item.value}
              {item.detail && !error && (
                <Typography
                  component="span"
                  typography="s2"
                  color="text.secondary"
                  sx={{ ml: 0.75 }}
                >
                  {item.detail}
                </Typography>
              )}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  );
}

StatStrip.propTypes = {
  label: PropTypes.string.isRequired,
  items: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.node,
      detail: PropTypes.node,
    }),
  ).isRequired,
  loading: PropTypes.bool,
  error: PropTypes.bool,
};
