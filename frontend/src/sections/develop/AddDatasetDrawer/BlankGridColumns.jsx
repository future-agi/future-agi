import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const TYPES = [
  ["text", "Text"],
  ["integer", "Integer"],
  ["float", "Float"],
  ["boolean", "Boolean"],
  ["json", "JSON"],
  ["array", "Array"],
  ["datetime", "Datetime"],
];

export default function BlankGridColumns({ columns, setColumns, roles }) {
  const patch = (index, change) =>
    setColumns((current) =>
      current.map((column, i) =>
        i === index ? { ...column, ...change } : column,
      ),
    );
  const names = columns.map((column) => column.name.trim().toLowerCase());
  return (
    <Box>
      <Typography typography="s2" fontWeight={600} sx={{ mb: 0.75 }}>
        Columns
      </Typography>
      <Box sx={{ display: "grid", gap: 1 }}>
        {columns.map((column, index) => {
          const invalid =
            !names[index] ||
            names.filter((name) => name === names[index]).length > 1;
          return (
            <Box
              key={index}
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "minmax(0, 1fr) minmax(0, 1fr) auto",
                  sm: "minmax(0, 1fr) 104px 112px auto",
                },
                gap: 1,
                alignItems: "start",
              }}
            >
              <TextField
                size="small"
                value={column.name}
                placeholder="column_name"
                onChange={(event) => patch(index, { name: event.target.value })}
                inputProps={{
                  "aria-label": `Column ${index + 1} name`,
                  maxLength: 2000,
                }}
                error={invalid}
                helperText={
                  invalid ? "Use a unique, non-empty name" : undefined
                }
                sx={{ gridColumn: { xs: "1 / -1", sm: "auto" }, minWidth: 0 }}
              />
              <Select
                size="small"
                value={column.type ?? "text"}
                onChange={(event) => patch(index, { type: event.target.value })}
                inputProps={{ "aria-label": `Column ${index + 1} type` }}
              >
                {TYPES.map(([value, label]) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </Select>
              <Select
                size="small"
                value={column.role}
                onChange={(event) => patch(index, { role: event.target.value })}
                inputProps={{ "aria-label": `Column ${index + 1} role` }}
              >
                {roles.map(({ value, label }) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </Select>
              <IconButton
                size="small"
                aria-label={`Remove column ${column.name || index + 1}`}
                disabled={columns.length === 1}
                onClick={() =>
                  setColumns((current) => current.filter((_, i) => i !== index))
                }
              >
                <Iconify icon="mdi:close" width={16} />
              </IconButton>
            </Box>
          );
        })}
      </Box>
      <Button
        size="small"
        startIcon={<Iconify icon="mdi:plus" width={16} />}
        onClick={() =>
          setColumns((current) => [
            ...current,
            {
              name: `column_${current.length + 1}`,
              type: "text",
              role: "metadata",
            },
          ])
        }
        disabled={columns.length >= 20}
        sx={{ mt: 0.75, typography: "s2" }}
      >
        Add column
      </Button>
    </Box>
  );
}

BlankGridColumns.propTypes = {
  columns: PropTypes.array.isRequired,
  setColumns: PropTypes.func.isRequired,
  roles: PropTypes.array.isRequired,
};
