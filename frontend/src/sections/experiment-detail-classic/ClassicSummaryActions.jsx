// `More` on the classic experiment Summary: Download…, Print or save as PDF
// and Copy summary, from the table this page shows (classicSummaryExport).
// The DSX SummaryToolbar is not mounted here: it reads the R5 analysis
// (verdicts, baselines, review queues) that the classic stats read lacks.
// The menu, toast, clipboard and print mechanism are the app's shared ones.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  GlobalStyles,
  Modal,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { ItemsMenu } from "src/sections/common/ux/oneview/MenuItems";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import { fileSizeText } from "src/sections/common/Analysis/analysisCsv";
import { fmtCount } from "src/sections/common/format/fmt";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import {
  saveFile,
  summaryCsv,
  summaryFileName,
  summaryJson,
  summaryTable,
  summaryText,
} from "./classicSummaryExport";

const NO_RUNS = "No runs in this summary yet";
const FORMATS = {
  csv: { label: "CSV", icon: "mdi:file-delimited-outline", type: "text/csv" },
  json: { label: "JSON", icon: "mdi:code-json", type: "application/json" },
};

// The print CSS of SummaryPrintView (Explore's print view): only the print
// root prints, A4 landscape, and its buttons stay off the page. (This root
// is a plain Box with no shadow to clear.)
const PRINT_CSS = {
  "@page": { size: "A4 landscape", margin: "12mm" },
  "@media print": {
    "body *": { visibility: "hidden !important" },
    "[data-print-root], [data-print-root] *": {
      visibility: "visible !important",
    },
    "[data-print-root]": {
      position: "absolute !important",
      inset: "0 auto auto 0 !important",
      width: "100% !important",
      overflow: "visible !important",
    },
    "[data-print-hide]": { display: "none !important" },
  },
};

const cellSx = {
  border: "1px solid",
  borderColor: "grey.300",
  px: 1,
  py: 0.5,
  typography: "s1",
  fontVariantNumeric: "tabular-nums",
  verticalAlign: "top",
};

/**
 * The printable Summary: the same table on a white page. A modal (no
 * backdrop): focus moves to Close and stays inside, the page behind is
 * inert, and Escape closes it.
 */
function ClassicSummaryPrint({ title, table, onClose }) {
  const date = new Date().toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const align = (i) => (i === table.nameIndex ? "left" : "right");
  return (
    <Modal
      open
      hideBackdrop
      disableRestoreFocus
      onClose={onClose}
      sx={{ zIndex: 2000 }}
    >
      <Box
        data-print-root
        data-testid="classic-summary-print"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        sx={{
          position: "fixed",
          inset: 0,
          bgcolor: "common.white",
          color: "common.black",
          overflowY: "auto",
          p: 3,
          outline: "none",
        }}
      >
        <GlobalStyles styles={PRINT_CSS} />
        <Stack
          direction="row"
          gap={1}
          justifyContent="flex-end"
          data-print-hide
          sx={{ mb: 1 }}
        >
          <Button
            variant="contained"
            size="small"
            onClick={() => window.print()}
          >
            Print or save as PDF
          </Button>
          {/* The Modal mounts its content through a portal after this
              component's effects run, so Close takes focus itself. */}
          <Button autoFocus size="small" onClick={onClose}>
            Close
          </Button>
        </Stack>
        <Typography typography="l3" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography typography="s1" sx={{ color: "grey.700" }}>
          {date} · {window.location.href}
        </Typography>
        <Box
          component="table"
          sx={{ borderCollapse: "collapse", width: "100%", mt: 2 }}
        >
          <thead>
            <tr>
              {table.headers.map((header, i) => (
                <Box
                  component="th"
                  key={`${i}-${header}`}
                  sx={{ ...cellSx, fontWeight: 600, textAlign: align(i) }}
                >
                  {header}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.lines.map((cells, row) => (
              <tr key={row}>
                {cells.map((cell, i) => (
                  <Box
                    component="td"
                    key={`${i}-${table.headers[i]}`}
                    sx={{ ...cellSx, textAlign: align(i) }}
                  >
                    {cell}
                  </Box>
                ))}
              </tr>
            ))}
          </tbody>
        </Box>
      </Box>
    </Modal>
  );
}

ClassicSummaryPrint.propTypes = {
  title: PropTypes.string.isRequired,
  table: PropTypes.shape({
    headers: PropTypes.arrayOf(PropTypes.string).isRequired,
    lines: PropTypes.arrayOf(PropTypes.arrayOf(PropTypes.string)).isRequired,
    nameIndex: PropTypes.number.isRequired,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
};

export default function ClassicSummaryActions({ columns, rows, name }) {
  const moreRef = useRef(null);
  // "more" | "download" | null: the open menu, both under the More button.
  const [menu, setMenu] = useState(null);
  const [printing, setPrinting] = useState(false);
  // Closing the print view gives focus back to More.
  const wasPrinting = useRef(false);
  useEffect(() => {
    if (wasPrinting.current && !printing) moreRef.current?.focus();
    wasPrinting.current = printing;
  }, [printing]);
  const table = useMemo(() => summaryTable(columns, rows), [columns, rows]);
  const title = name ? `${name} · ${COPY.summaryTitle}` : COPY.summaryTitle;
  const runs = fmtCount(table.lines.length, "run");
  const empty = table.lines.length === 0;

  const download = (format) => {
    const fileName = summaryFileName(name, format);
    const content = format === "json" ? summaryJson(table) : summaryCsv(table);
    const size = saveFile(content, fileName, FORMATS[format].type);
    notify(`Saved ${fileName} · ${runs} · ${fileSizeText(size)}`, {
      variant: "success",
      key: "classic-summary-download",
    });
  };
  const copySummary = async () => {
    const ok = await copyText(summaryText(table, title));
    notify(
      ok
        ? "Summary copied"
        : "Couldn't copy. Your browser blocked the clipboard.",
      { variant: ok ? "success" : "error", key: "classic-summary-copy" },
    );
  };
  // The print view is on the page before the browser's print dialog opens.
  const printSummary = () => {
    flushSync(() => setPrinting(true));
    window.print();
  };

  const moreItems = [
    {
      id: "download",
      label: "Download…",
      icon: "material-symbols:download",
      secondary: "The summary table as CSV or JSON",
      disabled: empty,
      reason: NO_RUNS,
      run: () => setMenu("download"),
    },
    {
      id: "print",
      label: "Print or save as PDF",
      icon: "mdi:printer-outline",
      secondary: "The summary table · A4 landscape",
      disabled: empty,
      reason: NO_RUNS,
      run: printSummary,
    },
    {
      id: "copy-summary",
      label: "Copy summary",
      icon: "mdi:content-copy",
      secondary: "Plain text with each run's values",
      disabled: empty,
      reason: NO_RUNS,
      run: copySummary,
    },
  ];
  const downloadItems = Object.entries(FORMATS).map(([format, f]) => ({
    id: format,
    label: f.label,
    icon: f.icon,
    secondary: `${runs} · the columns shown here`,
    run: () => download(format),
  }));
  const menuProps = {
    anchorEl: moreRef.current,
    onClose: () => setMenu(null),
    anchorOrigin: { vertical: "bottom", horizontal: "right" },
    transformOrigin: { vertical: "top", horizontal: "right" },
  };

  return (
    <>
      <Button
        ref={moreRef}
        variant="outlined"
        color="inherit"
        aria-haspopup="menu"
        aria-expanded={menu ? "true" : undefined}
        startIcon={<Iconify icon="mdi:dots-horizontal" width={18} />}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={() => setMenu("more")}
        sx={{ color: "text.secondary", borderColor: "divider" }}
      >
        More
      </Button>
      <ItemsMenu {...menuProps} open={menu === "more"} items={moreItems} />
      <ItemsMenu
        {...menuProps}
        open={menu === "download"}
        items={downloadItems}
      />
      {printing && (
        <ClassicSummaryPrint
          title={title}
          table={table}
          onClose={() => setPrinting(false)}
        />
      )}
    </>
  );
}

ClassicSummaryActions.propTypes = {
  // The grid's column defs and stats rows (ClassicExperimentSummary).
  columns: PropTypes.arrayOf(PropTypes.object).isRequired,
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  name: PropTypes.string,
};
