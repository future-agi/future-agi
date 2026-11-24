import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { diagnosisCauses } from "./fixRunView";

// What the diagnosis found, on a finished epoch: one folded line that opens to
// every cause. The design shows it only while the epoch runs; this keeps it.
export default function FinishedDiagnosis({ fix }) {
  const [open, setOpen] = useState(false);
  if (!fix.diagnosis) return null;
  const causes = diagnosisCauses(fix);
  const line = { typography: "s2", color: "text.secondary" };
  if (!causes.length) {
    return (
      <Typography data-testid="finished-diagnosis" sx={{ ...line, mb: 2 }}>
        The diagnosis named no single cause, so the candidates started from the
        failures.
      </Typography>
    );
  }
  return (
    <Box data-testid="finished-diagnosis" sx={{ mb: 2, minWidth: 0 }}>
      <ButtonBase
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        sx={{ gap: 0.75, maxWidth: 1, justifyContent: "flex-start" }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={12}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
          Diagnosis
        </Typography>
        {/* The summary only while folded: open, the list says it all. */}
        {!open && (
          <Typography noWrap sx={{ ...line, minWidth: 0 }}>
            {causes.length === 1
              ? causes[0]
              : `${causes[0]} · ${causes.length - 1} more`}
          </Typography>
        )}
      </ButtonBase>
      {open && (
        <Stack spacing={0.5} sx={{ mt: 0.75, pl: 2.5 }}>
          {causes.map((cause, i) => (
            // Two causes can read the same; the list never reorders.
            // eslint-disable-next-line react/no-array-index-key
            <Typography key={i} sx={line}>
              {cause}
            </Typography>
          ))}
        </Stack>
      )}
    </Box>
  );
}

FinishedDiagnosis.propTypes = { fix: PropTypes.object.isRequired };
