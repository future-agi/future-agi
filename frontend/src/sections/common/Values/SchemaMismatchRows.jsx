import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";

export default function SchemaMismatchRows({ rows, columns, rowIds, onClose }) {
  const selected = new Set(rowIds ?? []);
  return (
    <Dialog open={rowIds !== null} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Sampled rows that do not match</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          These are rows from the loaded schema sample, not a filtered dataset
          export.
        </Typography>
        {rows
          .filter((row) => selected.has(row.row_id))
          .map((row) => (
            <Box key={row.row_id} sx={{ mb: 2 }}>
              <Typography variant="subtitle2">
                Row {rows.indexOf(row) + 1}
              </Typography>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 1,
                  bgcolor: "action.hover",
                  color: "text.primary",
                  borderRadius: 1,
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontSize: "0.75rem",
                }}
              >
                {JSON.stringify(
                  Object.fromEntries(
                    columns.map((column) => [
                      column.name,
                      row[column.id]?.cell_value ?? null,
                    ]),
                  ),
                  null,
                  2,
                )}
              </Box>
            </Box>
          ))}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  );
}

SchemaMismatchRows.propTypes = {
  rows: PropTypes.array.isRequired,
  columns: PropTypes.array.isRequired,
  rowIds: PropTypes.array,
  onClose: PropTypes.func.isRequired,
};
