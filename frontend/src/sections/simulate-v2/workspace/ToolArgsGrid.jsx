import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { allowedLine } from "../_mock/rlContract";

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
}) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns:
          "minmax(140px, 260px) minmax(90px, 120px) 1fr auto",
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
};

function ArgCells({ arg, changedIn, onEdit }) {
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
        {`${TYPE_WORD[arg.type] || arg.type}${arg.required ? "" : " · optional"}`}
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
        {arg.note && (
          <Typography
            component="span"
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`· ${arg.note}`}</Typography>
        )}
        {arg.editedIn && changedIn && (
          <Typography
            component="span"
            sx={{ typography: "s3", color: "text.tertiary" }}
          >{`· changed in environment ${changedIn(arg.editedIn)}`}</Typography>
        )}
      </Stack>
      {onEdit ? (
        <Button
          size="small"
          onClick={onEdit}
          sx={{ minWidth: 0, color: "text.secondary" }}
        >
          Edit
        </Button>
      ) : (
        <span />
      )}
    </>
  );
}
ArgCells.propTypes = {
  arg: PropTypes.object.isRequired,
  changedIn: PropTypes.func,
  onEdit: PropTypes.func,
};
