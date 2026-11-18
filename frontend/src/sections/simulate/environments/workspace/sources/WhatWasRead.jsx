import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO_FONT } from "../contract/contract.constants";

const FIRST_LINES = 20;
const paged = (source) => ["pdf", "docx"].includes(source.ext);
const placeLabel = (source, n) =>
  paged(source) ? `Page ${n}` : `Section ${n}`;

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

// Passages in a two-column grid, the place shown on each page's first line.
function Passages({ source, passages, at }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "72px minmax(0, 1fr)",
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {passages.flatMap((passage, n) => {
        const lit = passage.id === at;
        const cell = {
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          ...(lit && { bgcolor: "action.selected" }),
        };
        return [
          <Typography
            key={`${passage.id}-where`}
            sx={{ ...cell, typography: "s3", color: "text.tertiary", pr: 1 }}
          >
            {n === 0 || passages[n - 1].page !== passage.page
              ? placeLabel(source, passage.page)
              : ""}
          </Typography>,
          <Box
            key={`${passage.id}-text`}
            ref={(el) => el && lit && el.scrollIntoView({ block: "center" })}
            sx={cell}
          >
            <Typography sx={{ typography: "s1" }}>{passage.text}</Typography>
            {lit && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                The change came from here
              </Typography>
            )}
          </Box>,
        ];
      })}
    </Box>
  );
}

Passages.propTypes = {
  source: PropTypes.object.isRequired,
  passages: PropTypes.array.isRequired,
  at: PropTypes.string,
};

function TextRead({ source, at }) {
  const { passages } = source.ui.parsed;
  const litIndex = passages.findIndex((passage) => passage.id === at);
  const [fullText, setFullText] = useState(litIndex >= 0);
  const [allLines, setAllLines] = useState(litIndex >= FIRST_LINES);
  const taken = passages.filter((passage) => passage.rule);
  const places = new Set(passages.map((passage) => passage.page)).size;
  const units = `${places} ${paged(source) ? "page" : "section"}${places === 1 ? "" : "s"}`;

  return (
    <Stack spacing={1.5}>
      <Box>
        <Heading label="What the builder took from it" count={taken.length} />
        {taken.length ? (
          <Passages source={source} passages={taken} at={at} />
        ) : (
          <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
            {source.role === "policy"
              ? "No sentence in it states a rule, so nothing is graded from it."
              : "Nothing is graded from it. The agent reads it as background."}
          </Typography>
        )}
      </Box>
      <Box>
        {fullText ? (
          <>
            <Heading
              label="Full text"
              count={`${units} · ${passages.length} lines`}
            />
            <Passages
              source={source}
              passages={allLines ? passages : passages.slice(0, FIRST_LINES)}
              at={at}
            />
            <Stack direction="row" spacing={2} sx={{ mt: 0.75 }}>
              {passages.length > FIRST_LINES && (
                <TextLink
                  label={
                    allLines
                      ? "Show fewer lines"
                      : `Show all ${passages.length} lines`
                  }
                  onClick={() => setAllLines((value) => !value)}
                />
              )}
              <TextLink
                label="Hide the full text"
                onClick={() => {
                  setFullText(false);
                  setAllLines(false);
                }}
              />
            </Stack>
          </>
        ) : (
          <TextLink
            label={`Show the full text · ${units}, ${passages.length} lines`}
            onClick={() => setFullText(true)}
          />
        )}
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
};

const markerOf = (column) => {
  if (column.key) return "key";
  if (column.ref) return `→ ${column.ref}`;
  if (column.sensitive) return "sensitive";
  return "";
};

function TableRead({ source, at }) {
  const parsed = source.ui.parsed;
  const atRow = Boolean(at) && Object.values(parsed.sample || {}).includes(at);

  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${parsed.format} · ${parsed.accepted.toLocaleString()} of ${parsed.rows.toLocaleString()} rows accepted`}
      </Typography>
      {atRow && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Row ${at}: the change came from this row`}
        </Typography>
      )}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          ...(atRow && { bgcolor: "action.selected" }),
        }}
      >
        {parsed.columns.map((column) => (
          <Stack
            key={column.name}
            direction="row"
            spacing={1.5}
            sx={{
              px: 1.25,
              py: 0.75,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Typography
              sx={{
                typography: "s1",
                fontFamily: MONO_FONT,
                width: 140,
                flexShrink: 0,
              }}
            >
              {column.name}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", width: 64 }}
            >
              {column.type}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", flex: 1, fontFamily: MONO_FONT }}
            >
              {String(parsed.sample?.[column.name] ?? "")}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {markerOf(column)}
            </Typography>
          </Stack>
        ))}
      </Box>
      {parsed.rejected.length > 0 && (
        <Box>
          <Typography sx={{ typography: "s3", fontWeight: 600, mt: 1 }}>
            {`Rejected rows · ${parsed.rejected.length}`}
          </Typography>
          {parsed.rejected.map((row) => (
            <Typography
              key={row.row}
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {`Row ${row.row}: ${row.reason}`}
            </Typography>
          ))}
          <Button
            size="small"
            variant="outlined"
            startIcon={
              <Iconify icon="solar:download-minimalistic-linear" width={14} />
            }
            onClick={() =>
              enqueueSnackbar(
                `Error report for ${source.name}: ${parsed.rejected.length} rows with reasons.`,
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

TableRead.propTypes = {
  source: PropTypes.object.isRequired,
  at: PropTypes.string,
};

/**
 * What reading the file found: a table's format, columns and rejected rows,
 * or the sentences the builder took from a document. `at` lights the cited
 * passage or sample row.
 */
export default function WhatWasRead({ source, at }) {
  const parsed = source.ui?.parsed;
  if (source.status === "reading" || !parsed)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {source.status === "reading" ? "Reading…" : source.status_note}
      </Typography>
    );
  return parsed.kind === "table" ? (
    <TableRead source={source} at={at} />
  ) : (
    <TextRead source={source} at={at} />
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
