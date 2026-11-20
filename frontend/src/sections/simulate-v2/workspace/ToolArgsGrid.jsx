import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { allowedLine, unitOf } from "../_mock/rlContract";

const MONO = "ui-monospace, Menlo, monospace";
const TYPE_WORD = {
  str: "text",
  int: "whole number",
  float: "number",
  bool: "yes / no",
  enum: "one of",
  date: "date",
  object: "object",
};

/*
  A tool's arguments as one grid — name, type, and the values the contract
  allows. World → Tools shows every argument of a tool; the Contract tab shows
  only the limits, each prefixed with its tool (`arg.tool`) and editable.
*/
export default function ToolArgsGrid({
  args,
  changedIn,
  onEdit,
  rowGap = 0.25,
  /* Name and type only as wide as they need, the allowed values take the
     rest, and a note sits on its own line — for a narrow drawer. */
  compact = false,
}) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: compact
          ? "max-content max-content minmax(0, 1fr) auto"
          : "minmax(140px, 260px) minmax(90px, 170px) 1fr auto",
        columnGap: 1.5,
        rowGap,
        alignItems: "baseline",
      }}
    >
      {args.map((a) => (
        <ArgCells
          key={`${a.tool || ""}.${a.name}`}
          arg={a}
          changedIn={changedIn}
          onEdit={onEdit ? () => onEdit(a) : null}
          compact={compact}
        />
      ))}
    </Box>
  );
}
ToolArgsGrid.propTypes = {
  args: PropTypes.array.isRequired,
  changedIn: PropTypes.func,
  onEdit: PropTypes.func,
  rowGap: PropTypes.number,
  compact: PropTypes.bool,
};

function ArgCells({ arg, changedIn, onEdit, compact = false }) {
  const chips = arg.type === "enum" ? arg.values || [] : null;
  return (
    <>
      <Typography
        sx={{
          typography: "s3",
          fontFamily: MONO,
          color: "text.secondary",
          minWidth: 0,
          overflowWrap: "anywhere",
        }}
      >
        {arg.tool && (
          <Box
            component="span"
            sx={{ color: "text.tertiary" }}
          >{`${arg.tool}.`}</Box>
        )}
        {arg.name}
        {!arg.required && (
          <Box component="span" sx={{ color: "text.disabled" }}>
            ?
          </Box>
        )}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {`${TYPE_WORD[arg.type] || arg.type}${unitOf(arg) ? ` · ${unitOf(arg)}` : ""}${arg.required ? "" : " · optional"}`}
      </Typography>
      <Stack
        direction="row"
        flexWrap="wrap"
        alignItems="baseline"
        sx={{ gap: 0.5, minWidth: 0 }}
      >
        {chips ? (
          chips.map((v) => (
            <Box
              key={v}
              component="span"
              sx={{
                px: 0.625,
                borderRadius: "4px",
                bgcolor: "background.neutral",
                typography: "s3",
                fontFamily: MONO,
                color: "text.primary",
              }}
            >
              {v}
            </Box>
          ))
        ) : (
          <Typography
            component="span"
            sx={{
              typography: "s3",
              fontFamily: arg.format ? MONO : undefined,
              color: "text.primary",
            }}
          >
            {allowedLine(arg)}
          </Typography>
        )}
        {arg.note &&
          (compact ? (
            /* Its own line under the values, capitalised as a sentence. */
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", width: 1 }}
            >
              {arg.note
                .replace(/^per call — /, "Per call. ")
                .replace(/^\w/, (c) => c.toUpperCase())}
            </Typography>
          ) : (
            <Typography
              component="span"
              sx={{ typography: "s3", color: "text.tertiary" }}
            >{`· ${arg.note}`}</Typography>
          ))}
        {arg.editedIn && changedIn && (
          <Typography
            component="span"
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`· changed in environment ${changedIn(arg.editedIn)}`}</Typography>
        )}
      </Stack>
      {onEdit ? (
        <Tooltip arrow title="Edit">
          <IconButton size="small" onClick={onEdit} aria-label="Edit">
            <Iconify icon="solar:pen-linear" width={16} />
          </IconButton>
        </Tooltip>
      ) : (
        <span />
      )}
    </>
  );
}
ArgCells.propTypes = {
  compact: PropTypes.bool,
  arg: PropTypes.object.isRequired,
  changedIn: PropTypes.func,
  onEdit: PropTypes.func,
};
