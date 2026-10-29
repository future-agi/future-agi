import { Box, Stack, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";
import { COMPAT_STATUS } from "../_mock/compatibility";
import {
  BENCHMARK_PARITY,
  SANDBOX_CHECKS,
  parityOf,
} from "../_mock/sandboxCert";

const box = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};
const row = {
  px: 1.5,
  py: 0.875,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

/**
 * Sandbox certification (roadmap release.md, Week 2): concurrency, cleanup,
 * isolation and lost-capability handling, each with what was run and what
 * it showed; then benchmark parity against published references.
 */
export default function SandboxCertification() {
  return (
    <Stack spacing={1.5}>
      <Box sx={box}>
        {SANDBOX_CHECKS.map((c) => (
          <Box
            key={c.id}
            sx={{
              ...row,
              display: "grid",
              gridTemplateColumns: "200px 112px minmax(0, 1fr)",
              columnGap: 2,
            }}
          >
            <Typography sx={{ typography: "s2" }}>{c.label}</Typography>
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <StatusDot
                status={COMPAT_STATUS[c.status].dot}
                size={6}
                live={false}
              />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {COMPAT_STATUS[c.status].label}
              </Typography>
            </Stack>
            <Box minWidth={0}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {c.showed}
              </Typography>
              {c.ran !== "—" && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >{`Ran: ${c.ran}`}</Typography>
              )}
            </Box>
          </Box>
        ))}
      </Box>
      <Box>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.tertiary",
            mb: 0.5,
          }}
        >
          Benchmark parity
        </Typography>
        <Box sx={box}>
          <Box
            sx={{
              ...row,
              display: "grid",
              gridTemplateColumns:
                "minmax(0, 1.4fr) 90px 90px minmax(0, 1fr) 140px",
              columnGap: 2,
            }}
          >
            {[
              "Benchmark",
              "Ours",
              "Published",
              "Reference agent",
              "Parity",
            ].map((h, i) => (
              <Typography
                key={h}
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  textAlign: i === 1 || i === 2 ? "right" : "left",
                }}
              >
                {h}
              </Typography>
            ))}
          </Box>
          {BENCHMARK_PARITY.map((b) => {
            const p = parityOf(b);
            return (
              <Box
                key={b.id}
                sx={{
                  ...row,
                  display: "grid",
                  gridTemplateColumns:
                    "minmax(0, 1.4fr) 90px 90px minmax(0, 1fr) 140px",
                  columnGap: 2,
                }}
              >
                <Typography sx={{ typography: "s2" }}>{b.name}</Typography>
                <Typography
                  sx={{
                    typography: "s2",
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {b.ours == null ? "—" : b.ours.toFixed(2)}
                </Typography>
                <Typography
                  sx={{
                    typography: "s2",
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {b.published.toFixed(2)}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s2", color: "text.secondary" }}
                >
                  {b.model}
                </Typography>
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <StatusDot
                    status={COMPAT_STATUS[p.status].dot}
                    size={6}
                    live={false}
                  />
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {p.line}
                  </Typography>
                </Stack>
              </Box>
            );
          })}
        </Box>
      </Box>
    </Stack>
  );
}
