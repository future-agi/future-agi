import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { glossaryEntry } from "src/sections/common/ux/glossary";
import { HeaderMenuButton } from "src/sections/common/ux/oneview";

const SORT_ICON = { asc: "mdi:arrow-up-bold", desc: "mdi:arrow-down-bold" };

/** The column's sort as React state (AG Grid `sortChanged`). */
export function useColumnSort(column) {
  const [sort, setSort] = useState(column?.getSort?.() ?? null);
  useEffect(() => {
    if (!column) return undefined;
    const onSort = () => setSort(column.getSort() ?? null);
    column.addEventListener("sortChanged", onSort);
    return () => column.removeEventListener("sortChanged", onSort);
  }, [column]);
  return sort;
}

/**
 * R3 header of the headline columns (F2, EL#10, EL#17): the name, with its
 * glossary definition and the formula in the header tooltip (one view: no
 * second hover target per header, so the header row reads as labels; the
 * verdict line above keeps its Terms), right-aligned over numbers, sort shown
 * only by a bold label + filled arrow + `aria-sort` (direction is never an
 * arrow). Click sorts, Shift adds a sort. G's ⋮ (hover / focus) opens the
 * column menu; right-click and Shift+F10 do too.
 */
const SummaryHeader = ({
  column,
  displayName,
  progressSort,
  enableSorting,
  enableMenu,
  showColumnMenu,
  termKey,
  tip,
  align = "right",
}) => {
  const sort = useColumnSort(column);
  const label = displayName;
  const def = termKey ? glossaryEntry(termKey)?.short : null;
  const title = [def, tip].filter(Boolean).join(" ");
  // G's ⋮ floats over the label's free side (no layout space), so labels
  // never truncate to make room for it; it shows on header hover / focus.
  const menuButton = (
    <HeaderMenuButton
      label={displayName}
      onOpen={(el) => showColumnMenu(el)}
      sx={{
        position: "absolute",
        top: "50%",
        transform: "translateY(-50%)",
        m: 0,
        bgcolor: "background.paper",
        ...(align === "right" ? { left: -6 } : { right: -6 }),
      }}
    />
  );
  return (
    <CustomTooltip
      show={!!title}
      title={title}
      size="small"
      placement="top"
      arrow
      enterDelay={500}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent={align === "right" ? "flex-end" : "flex-start"}
        gap={0.5}
        role="columnheader"
        className="fa-header-cell"
        // L2 G2 (a11y #13): ag-grid\'s columnheader carries aria-sort.
        onClick={(e) => {
          if (!enableSorting) return;
          progressSort(e.shiftKey);
        }}
        sx={{
          width: "100%",
          height: "100%",
          cursor: enableSorting ? "pointer" : "default",
          minWidth: 0,
          position: "relative",
        }}
      >
        <Typography
          component="span"
          typography="s1"
          fontWeight={sort ? "fontWeightBold" : "fontWeightMedium"}
          color="text.primary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {label}
        </Typography>
        {sort && (
          <Iconify
            icon={SORT_ICON[sort]}
            width={14}
            sx={{ color: "text.primary", flexShrink: 0 }}
          />
        )}
        {enableMenu && showColumnMenu && menuButton}
      </Stack>
    </CustomTooltip>
  );
};

SummaryHeader.propTypes = {
  column: PropTypes.object,
  displayName: PropTypes.string,
  progressSort: PropTypes.func,
  enableSorting: PropTypes.bool,
  enableMenu: PropTypes.bool,
  showColumnMenu: PropTypes.func,
  termKey: PropTypes.string,
  tip: PropTypes.string,
  align: PropTypes.oneOf(["left", "right"]),
};

export default SummaryHeader;
