import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  Collapse,
  MenuItem,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { READ_SAMPLES } from "src/api/simulate-environments/_fixtures/sources";
import { MONO_FONT } from "../contract/contract.constants";
import { plural } from "./sources.constants";

// Long files render a window of lines or rows, and grow on "Show more".
const LINES_CHUNK = 200;
const ROWS_CHUNK = 25;
// JSON opens this many levels deep; deeper objects start collapsed.
const OPEN_DEPTH = 2;
const MAX_CHILDREN = 50;
const SCROLL_BOX = { maxHeight: 440, overflowY: "auto" };

const collapseTimeout = (reduce) => (reduce ? 0 : "auto");

// A PDF reads by page; every other document by section, named by its heading.
const placeLabelOf = (source) => (page) =>
  source.ext === "pdf"
    ? `Page ${page}`
    : source.ui.parsed.headings?.[page] || `Section ${page}`;

function Heading({ label, count }) {
  return (
    <Typography sx={{ typography: "m3", fontWeight: 500, mb: 0.5 }}>
      {label}
      <Box
        component="span"
        sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
      >
        {count}
      </Box>
    </Typography>
  );
}

Heading.propTypes = {
  label: PropTypes.string.isRequired,
  count: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
};

function TextLink({ label, onClick }) {
  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        alignSelf: "flex-start",
        typography: "s1",
        fontWeight: 500,
        color: "text.secondary",
        textDecoration: "underline",
        textUnderlineOffset: 3,
      }}
    >
      {label}
    </ButtonBase>
  );
}

TextLink.propTypes = {
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
};

// Passages under a page or section heading, which sticks while its lines scroll.
function Passages({ passages, placeLabel, at }) {
  return (
    <Box>
      {passages.map((passage, n) => {
        const lit = passage.id === at;
        return (
          <Fragment key={passage.id}>
            {(n === 0 || passages[n - 1].page !== passage.page) && (
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 500,
                  color: "text.tertiary",
                  pt: 1,
                  pb: 0.5,
                  position: "sticky",
                  top: 0,
                  zIndex: 1,
                  bgcolor: "background.paper",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                {placeLabel(passage.page)}
              </Typography>
            )}
            <Box
              data-at={passage.id}
              sx={{
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
                ...(lit && { bgcolor: "action.selected" }),
              }}
            >
              <Typography sx={{ typography: "s1" }}>{passage.text}</Typography>
              {lit && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  The change came from here
                </Typography>
              )}
            </Box>
          </Fragment>
        );
      })}
    </Box>
  );
}

Passages.propTypes = {
  passages: PropTypes.array.isRequired,
  placeLabel: PropTypes.func.isRequired,
  at: PropTypes.string,
};

function TextRead({ source, at, reduce }) {
  const { passages } = source.ui.parsed;
  const placeLabel = placeLabelOf(source);
  const firstOf = (page) => passages.findIndex((one) => one.page === page);
  const litIndex = passages.findIndex((passage) => passage.id === at);
  const [fullText, setFullText] = useState(litIndex >= 0);
  // The lines on screen, [start, end): opens at the cited passage's place.
  const [range, setRange] = useState(() => {
    const start = litIndex >= 0 ? firstOf(passages[litIndex].page) : 0;
    return [start, Math.max(start + LINES_CHUNK, litIndex + 1)];
  });
  const boxRef = useRef(null);
  const [start, end] = range;

  useEffect(() => {
    if (at)
      boxRef.current
        ?.querySelector(`[data-at="${CSS.escape(at)}"]`)
        ?.scrollIntoView({ block: "center" });
  }, [at]);

  const taken = passages.filter((passage) => passage.rule);
  const places = [...new Set(passages.map((passage) => passage.page))];
  const units = `${places.length} ${source.ext === "pdf" ? "page" : "section"}${places.length === 1 ? "" : "s"}`;
  const lines = `${passages.length.toLocaleString()} line${passages.length === 1 ? "" : "s"}`;
  const left = passages.length - end;

  const jump = (page) => {
    const first = firstOf(page);
    setRange([first, first + LINES_CHUNK]);
    if (boxRef.current) boxRef.current.scrollTop = 0;
  };

  return (
    <Stack spacing={1.5}>
      <Box>
        <Heading label="What the builder took from it" count={taken.length} />
        {taken.length ? (
          <Box sx={SCROLL_BOX}>
            <Passages passages={taken} placeLabel={placeLabel} at={at} />
          </Box>
        ) : (
          <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
            {source.role === "policy"
              ? "No sentence in it states a rule, so nothing is graded from it."
              : "Nothing is graded from it. The agent reads it as background."}
          </Typography>
        )}
      </Box>
      <Box>
        <Collapse in={!fullText} timeout={collapseTimeout(reduce)}>
          <TextLink
            label={`Show the full text · ${units}, ${lines}`}
            onClick={() => setFullText(true)}
          />
        </Collapse>
        <Collapse in={fullText} timeout={collapseTimeout(reduce)} unmountOnExit>
          <Stack
            direction="row"
            alignItems="flex-end"
            justifyContent="space-between"
            spacing={1}
          >
            <Heading label="Full text" count={`${units} · ${lines}`} />
            {places.length > 1 && (
              <TextField
                select
                size="small"
                label={source.ext === "pdf" ? "Go to page" : "Go to section"}
                value={passages[start].page}
                onChange={(event) => jump(event.target.value)}
                sx={{ minWidth: 160, maxWidth: 220 }}
              >
                {places.map((page) => (
                  <MenuItem key={page} value={page}>
                    {placeLabel(page)}
                  </MenuItem>
                ))}
              </TextField>
            )}
          </Stack>
          <Box ref={boxRef} sx={{ ...SCROLL_BOX, mt: 0.75 }}>
            {start > 0 && (
              <Box sx={{ py: 0.75 }}>
                <TextLink
                  label={`Show ${Math.min(LINES_CHUNK, start)} earlier lines`}
                  onClick={() =>
                    setRange([Math.max(0, start - LINES_CHUNK), end])
                  }
                />
              </Box>
            )}
            <Passages
              passages={passages.slice(start, end)}
              placeLabel={placeLabel}
              at={at}
            />
            {left > 0 && (
              <Box sx={{ py: 0.75 }}>
                <TextLink
                  label={`Show ${Math.min(LINES_CHUNK, left)} more lines · ${left.toLocaleString()} left`}
                  onClick={() => setRange([start, end + LINES_CHUNK])}
                />
              </Box>
            )}
          </Box>
          <Box sx={{ mt: 0.75 }}>
            <TextLink
              label="Hide the full text"
              onClick={() => setFullText(false)}
            />
          </Box>
        </Collapse>
      </Box>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${source.ui.effective_from ? `Effective from ${source.ui.effective_from}. ` : ""}Instructions inside a document are read as content only.`}
      </Typography>
    </Stack>
  );
}

TextRead.propTypes = {
  source: PropTypes.object.isRequired,
  at: PropTypes.string,
  reduce: PropTypes.bool,
};

const markerOf = (column) => {
  if (column.key) return "key";
  if (column.ref) return `→ ${column.ref}`;
  if (column.sensitive) return "sensitive";
  return "";
};

const cellText = (value) => {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
};

// One table's first rows under its column headers, its row count and the
// rows the reader rejected.
function SheetRead({ source, sheet, at }) {
  // A real read sends one sample row; the dev samples send their first rows.
  const rows = sheet.preview || [sheet.sample || {}];
  const litRow = at
    ? rows.findIndex((row) => Object.values(row).includes(at))
    : -1;
  const [shown, setShown] = useState(Math.max(ROWS_CHUNK, litRow + 1));

  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${sheet.format} · ${sheet.accepted.toLocaleString()} of ${sheet.rows.toLocaleString()} rows accepted`}
      </Typography>
      {litRow >= 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Row ${at}: the change came from this row`}
        </Typography>
      )}
      <Box sx={{ ...SCROLL_BOX, overflowX: "auto" }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              {sheet.columns.map((column) => (
                <TableCell key={column.name}>
                  <Box sx={{ fontFamily: MONO_FONT, whiteSpace: "nowrap" }}>
                    {column.name}
                  </Box>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {[column.type, markerOf(column)]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.slice(0, shown).map((row, index) => (
              // Preview rows have no id of their own; their order is fixed.
              // eslint-disable-next-line react/no-array-index-key
              <TableRow key={index} selected={index === litRow}>
                {sheet.columns.map((column) => (
                  <TableCell
                    key={column.name}
                    sx={{
                      fontFamily: MONO_FONT,
                      whiteSpace: "nowrap",
                      maxWidth: 220,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {cellText(row[column.name])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      <Stack direction="row" spacing={2} alignItems="center">
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`First ${Math.min(shown, rows.length).toLocaleString()} of ${sheet.rows.toLocaleString()} rows`}
        </Typography>
        {shown < rows.length && (
          <TextLink
            label="Show more rows"
            onClick={() => setShown((value) => value + ROWS_CHUNK)}
          />
        )}
      </Stack>
      {sheet.rejected.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 600, mt: 1 }}>
            {`Rejected rows · ${sheet.rejected.length}`}
          </Typography>
          <Box sx={{ maxHeight: 160, overflowY: "auto" }}>
            {sheet.rejected.map((row) => (
              <Typography
                key={row.row}
                sx={{ typography: "s3", color: "text.secondary" }}
              >
                {`Row ${row.row}: ${row.reason}`}
              </Typography>
            ))}
          </Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={
              <Iconify icon="solar:download-minimalistic-linear" width={14} />
            }
            onClick={() =>
              enqueueSnackbar(
                `Error report for ${source.name}: ${sheet.rejected.length} rows with reasons.`,
                { variant: "success" },
              )
            }
            sx={{ mt: 0.5 }}
          >
            Download error report
          </Button>
        </Box>
      )}
    </Stack>
  );
}

SheetRead.propTypes = {
  source: PropTypes.object.isRequired,
  sheet: PropTypes.object.isRequired,
  at: PropTypes.string,
};

// A spreadsheet with several sheets gets a tab per sheet.
function TableRead({ source, at }) {
  const { parsed } = source.ui;
  const [sheetIndex, setSheetIndex] = useState(0);
  if (!parsed.sheets)
    return <SheetRead source={source} sheet={parsed} at={at} />;
  return (
    <Stack spacing={1}>
      <CustomTabs
        value={sheetIndex}
        onChange={(_, index) => setSheetIndex(index)}
        variant="scrollable"
        scrollButtons={false}
        sx={{ boxShadow: "none" }}
      >
        {parsed.sheets.map((sheet, index) => (
          <Tab key={sheet.name} value={index} label={sheet.name} />
        ))}
      </CustomTabs>
      <SheetRead
        key={sheetIndex}
        source={source}
        sheet={parsed.sheets[sheetIndex]}
        at={at}
      />
    </Stack>
  );
}

TableRead.propTypes = {
  source: PropTypes.object.isRequired,
  at: PropTypes.string,
};

function TreeNode({ name, value, depth, reduce }) {
  const [open, setOpen] = useState(depth < OPEN_DEPTH);
  const keyLabel = (
    <Box component="span" sx={{ color: "text.tertiary" }}>
      {`${name}: `}
    </Box>
  );
  if (value === null || typeof value !== "object")
    return (
      <Typography sx={{ typography: "s1", fontFamily: MONO_FONT, pl: 2.25 }}>
        {keyLabel}
        {JSON.stringify(value)}
      </Typography>
    );

  const entries = Object.entries(value);
  return (
    <Box>
      <ButtonBase
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        sx={{ typography: "s1", fontFamily: MONO_FONT, gap: 0.5 }}
      >
        <Iconify
          icon="solar:alt-arrow-right-linear"
          width={14}
          sx={{
            transform: open ? "rotate(90deg)" : "none",
            transition: (theme) => theme.transitions.create("transform"),
            "@media (prefers-reduced-motion: reduce)": { transition: "none" },
          }}
        />
        {keyLabel}
        <Box component="span" sx={{ color: "text.tertiary" }}>
          {Array.isArray(value)
            ? `[${plural(entries.length, "item")}]`
            : `{${plural(entries.length, "key")}}`}
        </Box>
      </ButtonBase>
      <Collapse in={open} timeout={collapseTimeout(reduce)} unmountOnExit>
        <Box
          sx={{
            ml: 0.75,
            pl: 1.25,
            borderLeft: "1px solid",
            borderColor: "divider",
          }}
        >
          {entries.slice(0, MAX_CHILDREN).map(([key, child]) => (
            <TreeNode
              key={key}
              name={key}
              value={child}
              depth={depth + 1}
              reduce={reduce}
            />
          ))}
          {entries.length > MAX_CHILDREN && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", pl: 2.25 }}
            >
              {`and ${entries.length - MAX_CHILDREN} more`}
            </Typography>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}

TreeNode.propTypes = {
  name: PropTypes.string.isRequired,
  value: PropTypes.any,
  depth: PropTypes.number.isRequired,
  reduce: PropTypes.bool,
};

// A JSON object as a key/value tree.
function TreeRead({ source, reduce }) {
  const { parsed } = source.ui;
  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {parsed.format}
      </Typography>
      <Box sx={SCROLL_BOX}>
        {Object.entries(parsed.value).map(([key, child]) => (
          <TreeNode
            key={key}
            name={key}
            value={child}
            depth={0}
            reduce={reduce}
          />
        ))}
      </Box>
    </Stack>
  );
}

TreeRead.propTypes = {
  source: PropTypes.object.isRequired,
  reduce: PropTypes.bool,
};

const READERS = { table: TableRead, tree: TreeRead, text: TextRead };

/**
 * What reading the file found: a table's first rows and rejected rows, a JSON
 * object as a tree, or a document by page or section with the sentences the
 * builder took from it. `at` lights the cited passage or sample row. In dev a
 * "Sample (dev only)" select swaps in a sample file for what's shown here only.
 */
export default function WhatWasRead({ source, at }) {
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [sampleId, setSampleId] = useState("");
  const sample = READ_SAMPLES.find((one) => one.id === sampleId);
  const sampleParsed = useMemo(() => sample?.parsed(), [sample]);
  const shown = sample
    ? {
        ...source,
        ext: sample.ext,
        status: "not_saved",
        ui: { ...source.ui, parsed: sampleParsed, effective_from: null },
      }
    : source;
  const parsed = shown.ui?.parsed;
  const Reader = READERS[parsed?.kind] || TextRead;

  return (
    <Stack spacing={1.5}>
      {import.meta.env.DEV && (
        <TextField
          select
          size="small"
          label="Sample (dev only)"
          value={sampleId}
          onChange={(event) => setSampleId(event.target.value)}
          SelectProps={{ displayEmpty: true }}
          InputLabelProps={{ shrink: true }}
          sx={{ maxWidth: 240 }}
        >
          <MenuItem value="">This file</MenuItem>
          {READ_SAMPLES.map((one) => (
            <MenuItem key={one.id} value={one.id}>
              {one.label}
            </MenuItem>
          ))}
        </TextField>
      )}
      {shown.status === "reading" || !parsed ? (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {shown.status === "reading" ? "Reading…" : shown.status_note}
        </Typography>
      ) : (
        <Reader
          key={sampleId}
          source={shown}
          at={sample ? undefined : at}
          reduce={reduce}
        />
      )}
    </Stack>
  );
}

WhatWasRead.propTypes = {
  source: PropTypes.shape({
    name: PropTypes.string.isRequired,
    status: PropTypes.string,
    status_note: PropTypes.string,
    ui: PropTypes.object,
  }).isRequired,
  at: PropTypes.string,
};
