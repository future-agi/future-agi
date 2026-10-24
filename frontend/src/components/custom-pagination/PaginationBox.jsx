import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const PaginationBox = ({
  page,
  totalPages,
  onPageChange,
  totalItems,
  itemsPerPage,
}) => {
  const startItem = (page - 1) * itemsPerPage + 1;
  const endItem = Math.min(page * itemsPerPage, totalItems);

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        gap: 2,
        py: 2,
        borderTop: "1px solid",
        borderColor: "divider",
        mt: "auto",
        color: "text.primary",
      }}
    >
      {/* Items counter */}
      <Typography fontSize={"12px"} color="inherit">
        <b>{startItem}</b> to <b>{endItem}</b> of <b>{totalItems}</b>
      </Typography>

      {/* Navigation controls */}
      <Box sx={{ display: "flex", alignItems: "center", color: "inherit" }}>
        {/* First page */}
        <IconButton
          onClick={() => onPageChange(1)}
          disabled={page === 1}
          size="small"
          sx={{
            color: page === 1 ? "text.disabled" : "inherit",
          }}
        >
          <Iconify icon="solar:chevron-first-linear" />
        </IconButton>

        {/* Previous page */}
        <IconButton
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
          size="small"
          sx={{
            color: page === 1 ? "text.disabled" : "inherit",
          }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" />
        </IconButton>

        {/* Page counter */}
        <Typography
          sx={{
            mx: 2,
            display: "flex",
            alignItems: "center",
            gap: 1,
            fontSize: "12px",
            color: "inherit",
            fontWeight: 400,
          }}
        >
          Page <b>{page}</b> of <b>{totalPages}</b>
        </Typography>

        {/* Next page */}
        <IconButton
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          size="small"
          sx={{
            color: page >= totalPages ? "text.disabled" : "inherit",
          }}
        >
          <Iconify icon="solar:alt-arrow-right-linear" />
        </IconButton>

        {/* Last page */}
        <IconButton
          onClick={() => onPageChange(totalPages)}
          disabled={page >= totalPages}
          size="small"
          sx={{
            color: page >= totalPages ? "text.disabled" : "inherit",
          }}
        >
          <Iconify icon="solar:chevron-last-linear" />
        </IconButton>
      </Box>
    </Box>
  );
};

PaginationBox.propTypes = {
  page: PropTypes.number.isRequired,
  totalPages: PropTypes.number.isRequired,
  onPageChange: PropTypes.func.isRequired,
  totalItems: PropTypes.number.isRequired,
  itemsPerPage: PropTypes.number.isRequired,
};

export default PaginationBox;
