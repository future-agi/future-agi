import { tableRowClasses } from "@mui/material/TableRow";

// ----------------------------------------------------------------------

export function table(theme) {
  return {
    MuiTableContainer: {
      styleOverrides: {
        root: {
          position: "relative",
        },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          [`&.${tableRowClasses.selected}`]: {
            backgroundColor: theme.palette.action.selected,
            "&:hover": {
              backgroundColor: theme.palette.action.selected,
            },
          },
          [`&.${tableRowClasses.hover}:hover`]: {
            backgroundColor: theme.palette.action.hover,
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        // Design system table: no outer box, 12px cells in fg.secondary in 32px
        // rows, a border.default line under every row; 32px header in 11px/500
        // fg.tertiary with no fill.
        root: {
          fontSize: 12,
          lineHeight: "18px",
          color: theme.palette.text.secondary,
          borderBottom: `1px solid ${theme.palette.border.default}`,
          paddingTop: 7,
          paddingBottom: 7,
          paddingLeft: 12,
          paddingRight: 12,
        },
        sizeSmall: {
          paddingTop: 5,
          paddingBottom: 5,
        },
        head: {
          fontSize: 11,
          lineHeight: "16px",
          height: 32,
          paddingTop: 0,
          paddingBottom: 0,
          color: theme.palette.text.subtitle,
          fontWeight: 500,
          backgroundColor: "transparent",
          borderBottom: `1px solid ${theme.palette.border.default}`,
        },
        stickyHeader: {
          backgroundColor: theme.palette.background.paper,
        },
        paddingCheckbox: {
          paddingLeft: theme.spacing(1),
        },
      },
    },
    MuiTablePagination: {
      styleOverrides: {
        root: {
          width: "100%",
        },
        toolbar: {
          minHeight: 44,
          height: 44,
          fontSize: 11,
          color: theme.palette.text.subtitle,
        },
        actions: {
          marginRight: 8,
        },
        select: {
          paddingLeft: 8,
          "&:focus": {
            borderRadius: theme.shape.borderRadius,
          },
        },
        selectIcon: {
          right: 4,
          width: 16,
          height: 16,
          top: "calc(50% - 8px)",
        },
      },
    },
  };
}
