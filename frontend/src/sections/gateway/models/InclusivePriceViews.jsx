import React from "react";
import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";
import { inclusivePriceRows } from "./inclusivePricing";
import { FactGrid } from "./detail/Section";
import { pricingColumns } from "./detail/pricingColumns";

// Each dimension shows one price: what a request on Future AGI credits is
// charged per billing unit. The provider's own quote and the margin are never
// shown beside it.

// The v2 grid's spanning price cell. Its column opts into auto-height so no
// dimension disappears below the former fixed-height token row.
export function InclusivePriceCell({ model, markupPercent }) {
  const rows = inclusivePriceRows(model, markupPercent);
  if (!rows.length) {
    return (
      <Tooltip
        title="No fixed list price: each request is billed from the usage the provider reports."
        arrow
      >
        <Typography typography="s2" color="text.secondary" component="span">
          Varies
        </Typography>
      </Tooltip>
    );
  }
  return (
    <Stack
      spacing={0.5}
      alignItems="flex-end"
      aria-label="Prices by billing unit"
      sx={{ width: "100%", minWidth: 0, py: 1, whiteSpace: "normal" }}
    >
      {rows.map((row) => (
        <Typography
          key={row.key}
          typography="s2"
          component="span"
          sx={{
            fontVariantNumeric: "tabular-nums",
            overflowWrap: "anywhere",
            lineHeight: 1.4,
          }}
        >
          {`${row.label}: ${row.charged} / ${row.unit}`}
        </Typography>
      ))}
    </Stack>
  );
}

InclusivePriceCell.propTypes = {
  model: PropTypes.object.isRequired,
  markupPercent: PropTypes.number,
};

// SC2: use v2 FactGrid and DS typography, never resurrect the legacy MUI
// Table/body2 drawer. Decimal strings stay strings through the grid formatter.
export function InclusivePricingTable({ model, markupPercent }) {
  const rows = inclusivePriceRows(model, markupPercent);
  if (!rows.length) {
    return (
      <Typography typography="s2" color="text.secondary">
        This router has no fixed list price. Requests on Future AGI credits are
        billed from the usage the provider reports for each request.
      </Typography>
    );
  }
  return (
    <>
      <FactGrid
        rowData={rows.map((row) => ({
          key: row.key,
          label: `${row.label} (per ${row.unit})`,
          managed: row.charged,
        }))}
        columnDefs={pricingColumns(true, { inclusive: true })}
      />
      <Typography typography="s2" color="text.secondary" component="p">
        Prices on Future AGI credits, per billing unit. A request is billed
        from the usage the provider reports, not a sum of these rows. With your
        own key, your provider bills you directly.
      </Typography>
    </>
  );
}

InclusivePricingTable.propTypes = InclusivePriceCell.propTypes;
