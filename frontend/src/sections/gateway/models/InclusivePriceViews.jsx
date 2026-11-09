import React from "react";
import PropTypes from "prop-types";
import { Stack, Tooltip, Typography } from "@mui/material";
import { formatPercent } from "./catalog";
import { inclusivePriceRows } from "./inclusivePricing";
import { FactGrid } from "./detail/Section";
import { pricingColumns } from "./detail/pricingColumns";

// The v2 grid's spanning price cell. Its column opts into auto-height so no
// dimension disappears below the former fixed-height token row.
export function InclusivePriceCell({ model, markupPercent }) {
  const rows = inclusivePriceRows(model, markupPercent);
  if (!rows.length) {
    return (
      <Tooltip
        title={`Priced per request by the provider. Billed the provider's inclusive total + ${formatPercent(markupPercent)}.`}
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
        <Tooltip
          key={row.key}
          title={`Provider price: ${row.provider} / ${row.unit}`}
          arrow
        >
          <Typography
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
        </Tooltip>
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
        billed the provider&apos;s reported inclusive total +{" "}
        {formatPercent(markupPercent)}.
      </Typography>
    );
  }
  return (
    <>
      <FactGrid
        rowData={rows.map((row) => ({
          ...row,
          label: `${row.label} (per ${row.unit})`,
          managed: row.charged,
        }))}
        columnDefs={pricingColumns(true, markupPercent, { inclusive: true })}
      />
      <Typography typography="s2" color="text.secondary" component="p">
        These are the provider&apos;s quoted dimensions. Billing uses the
        provider&apos;s reported inclusive total +{" "}
        {formatPercent(markupPercent)}, not a sum of these rows. With your own
        key, your provider bills you directly.
      </Typography>
    </>
  );
}

InclusivePricingTable.propTypes = InclusivePriceCell.propTypes;
