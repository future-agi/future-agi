import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";

const MONO = "ui-monospace, Menlo, monospace";
const TYPE_WORD = {
  str: "text",
  string: "text",
  int: "whole number",
  integer: "whole number",
  float: "number",
  number: "number",
  bool: "yes / no",
  boolean: "yes / no",
  enum: "one of",
  date: "date",
  object: "object",
  dict: "object",
  list: "list",
  array: "list",
};

// A real tool's arguments in the grid's shape: its declared names, typed from
// arg_types, with the values arg_values allows. Whether an argument is
// optional isn't reported, so `required` stays undefined and nothing is marked.
export const realToolArgs = (tool) =>
  (tool?.args || []).map((name) => {
    const values = tool?.argValues?.[name];
    return {
      name,
      type: Array.isArray(values) && values.length ? "enum" : tool?.argTypes?.[name] || null,
      values: Array.isArray(values) ? values.map(String) : null,
    };
  });

// Vel's argument grid: name, type, and the values the contract allows. The
// Contract tab lists only the limits, each prefixed with its tool, with Edit.
export default function ToolArgsGrid({ args, onEdit, rowGap = 0.25 }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: `minmax(140px, 260px) minmax(90px, 120px) 1fr${onEdit ? " auto" : ""}`,
        columnGap: 1.5,
        rowGap,
        alignItems: "baseline",
      }}
    >
      {args.map((a) => (
        <ArgCells key={`${a.tool || ""}.${a.name}`} arg={a} onEdit={onEdit ? () => onEdit(a) : null} />
      ))}
    </Box>
  );
}
ToolArgsGrid.propTypes = { args: PropTypes.array.isRequired, onEdit: PropTypes.func, rowGap: PropTypes.number };

function ArgCells({ arg, onEdit }) {
  const optional = arg.required === false;
  const typeWord = arg.type ? TYPE_WORD[arg.type] || arg.type : "-";
  return (
    <>
      <Typography
        sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary", minWidth: 0, overflowWrap: "anywhere" }}
      >
        {arg.tool && (
          <Box component="span" sx={{ color: "text.subtitle" }}>{`${arg.tool}.`}</Box>
        )}
        {arg.name}
        {optional && (
          <Box component="span" sx={{ color: "text.disabled" }}>
            ?
          </Box>
        )}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {`${typeWord}${optional ? " · optional" : ""}`}
      </Typography>
      <Stack direction="row" flexWrap="wrap" alignItems="baseline" sx={{ gap: 0.5, minWidth: 0 }}>
        {arg.values?.length ? (
          arg.values.map((v) => (
            <Box
              key={v}
              component="span"
              sx={{ px: 0.625, borderRadius: "4px", bgcolor: "background.neutral", typography: "s3", fontFamily: MONO, color: "text.primary" }}
            >
              {v}
            </Box>
          ))
        ) : arg.allowed ? (
          <Typography component="span" sx={{ typography: "s3", fontFamily: arg.mono ? MONO : undefined, color: "text.primary" }}>
            {arg.allowed}
          </Typography>
        ) : null}
      </Stack>
      {onEdit && (
        <Button size="small" onClick={onEdit} sx={{ minWidth: 0, color: "text.secondary" }}>
          Edit
        </Button>
      )}
    </>
  );
}
ArgCells.propTypes = { arg: PropTypes.object.isRequired, onEdit: PropTypes.func };
