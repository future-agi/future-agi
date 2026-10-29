import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import SideDrawer from "../../components/SideDrawer";

import { OUTCOME_LABEL, checksLine } from "./fixRunView";

// "How it was decided": every trial, why it was kept or turned away, and the
// checks it went through, from each candidate's verdict.
export default function FixDecisionDrawer({ fix, open, onClose }) {
  const trials = [...(fix.candidates || [])].sort(
    (a, b) => a.attempt - b.attempt,
  );
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Box data-testid="fix-decision" sx={{ p: 2.5, pr: 5 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600, mb: 2 }}>
          How it was decided
        </Typography>
        <Stack spacing={2}>
          {trials.map((c) => (
            <Box
              key={c.id}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                p: 1.5,
              }}
            >
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                {`Trial ${c.attempt} · ${OUTCOME_LABEL[c.outcome] || c.outcome}`}
              </Typography>
              {c.verdict.reason && (
                <Typography
                  sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
                >
                  {c.verdict.reason}
                </Typography>
              )}
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}
              >
                {checksLine(c.verdict.tiers)}
              </Typography>
              {c.verdict.tiers.map((t) => (
                <Typography
                  key={t.name}
                  sx={{ typography: "s3", color: "text.secondary" }}
                >
                  {`${t.name} · ${t.status}${t.detail ? ` · ${t.detail}` : ""}`}
                </Typography>
              ))}
            </Box>
          ))}
        </Stack>
      </Box>
    </SideDrawer>
  );
}

FixDecisionDrawer.propTypes = {
  fix: PropTypes.object.isRequired,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
