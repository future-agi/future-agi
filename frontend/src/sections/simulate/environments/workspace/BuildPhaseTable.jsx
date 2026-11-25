import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const MARK = {
  done: { icon: "solar:check-circle-linear", color: "status.success.fg" },
  now: { icon: "solar:refresh-linear", color: "text.primary" },
  stopped: { icon: "solar:stop-circle-linear", color: "text.primary" },
  next: { icon: "solar:clock-circle-linear", color: "text.tertiary" },
};

const ACTIVE = new Set(["now", "stopped"]);

export default function BuildPhaseTable({ rows }) {
  return (
    <Box
      component="table"
      sx={{
        width: "100%",
        borderCollapse: "collapse",
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box component="tbody">
        {rows.map((row) => {
          const mark = MARK[row.state];
          const active = ACTIVE.has(row.state);
          return (
            <Box
              component="tr"
              key={row.id}
              data-state={row.state}
              sx={{
                "& + tr > td": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Box component="td" sx={{ pl: 2, pr: 1, py: 0.75, width: 280 }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Iconify
                    icon={mark.icon}
                    width={14}
                    sx={{ color: mark.color, flexShrink: 0 }}
                  />
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: active ? 600 : 400,
                      color:
                        row.state === "next" ? "text.tertiary" : "text.primary",
                    }}
                  >
                    {row.label}
                  </Typography>
                </Stack>
              </Box>
              <Box
                component="td"
                sx={{
                  px: 1,
                  py: 0.75,
                  width: 96,
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {row.range}
              </Box>
              <Box
                component="td"
                sx={{
                  pl: 1,
                  pr: 2,
                  py: 0.75,
                  typography: "s3",
                  color: active ? "text.primary" : "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {row.note}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

BuildPhaseTable.propTypes = {
  rows: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      range: PropTypes.string.isRequired,
      state: PropTypes.oneOf(["done", "now", "stopped", "next"]).isRequired,
      note: PropTypes.string.isRequired,
    }),
  ).isRequired,
};
