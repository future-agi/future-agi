// ----------------------------------------------------------------------

export function cssBaseline() {
  return {
    MuiCssBaseline: {
      styleOverrides: {
        "*": {
          boxSizing: "border-box",
        },
        html: {
          margin: 0,
          padding: 0,
          width: "100%",
          height: "100%",
          WebkitOverflowScrolling: "touch",
        },
        body: {
          margin: 0,
          padding: 0,
          width: "100%",
          height: "100%",
        },
        // Design system: row actions show on row hover or keyboard focus,
        // so a table doesn't carry a button on every row. Mark the action
        // with className="row-hover-action".
        ".row-hover-action": {
          opacity: 0,
          transition: "opacity 120ms",
        },
        [[
          "tr:hover .row-hover-action",
          "tr:focus-within .row-hover-action",
          ".MuiDataGrid-row:hover .row-hover-action",
          ".MuiDataGrid-row:focus-within .row-hover-action",
          ".ag-row-hover .row-hover-action",
          ".ag-row-focus .row-hover-action",
          ".row-hover-action:focus-visible",
        ].join(", ")]: {
          opacity: 1,
        },
        "@media (hover: none)": {
          ".row-hover-action": { opacity: 1 },
        },
        "#root, #__next": {
          width: "100%",
          height: "100%",
        },
        input: {
          "&[type=number]": {
            MozAppearance: "textfield",
            "&::-webkit-outer-spin-button": {
              margin: 0,
              WebkitAppearance: "none",
            },
            "&::-webkit-inner-spin-button": {
              margin: 0,
              WebkitAppearance: "none",
            },
          },
        },
        img: {
          maxWidth: "100%",
          display: "inline-block",
          verticalAlign: "bottom",
        },
      },
    },
  };
}
