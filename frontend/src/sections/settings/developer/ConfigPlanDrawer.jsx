import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "src/sections/simulate-v2/components/SideDrawer";
import { OP_LABEL, PROFILES, SAMPLE_PLAN } from "./configPlan";

/*
  Review a settings plan before it's applied (T38): the workspace it targets
  by profile, every change as a diff, and what each change can and can't be
  rolled back to. A change that can't be undone needs its own tick. Nothing
  here deploys the agent.
*/
const OP_COLOR = {
  add: "status.success.fg",
  change: "text.secondary",
  remove: "status.danger.fg",
};

export default function ConfigPlanDrawer({ open, onClose }) {
  const plan = SAMPLE_PLAN;
  const [ack, setAck] = useState(false);
  const [applied, setApplied] = useState(false);
  const risky = plan.changes.some((c) => c.danger);
  const cell = {
    typography: "s2",
    py: 0.875,
    px: 1,
    borderBottom: 1,
    borderColor: "divider",
    verticalAlign: "top",
  };
  return (
    <SideDrawer open={open} onClose={onClose} width={760}>
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
              {`Plan ${plan.id} · profile ${plan.profile}`}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              {`${PROFILES[plan.profile].workspace} workspace · from ${plan.file}`}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>

        <Stack
          spacing={2}
          sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2.5 }}
        >
          <Stack
            direction="row"
            spacing={1}
            alignItems="flex-start"
            sx={{
              px: 1.5,
              py: 1,
              borderRadius: 1,
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={16}
              sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
            />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Changes this workspace&apos;s settings only. It never deploys or
              changes your agent, and simulation environments aren&apos;t
              touched.
            </Typography>
          </Stack>

          <Box component="table" sx={{ width: 1, borderCollapse: "collapse" }}>
            <Box component="thead">
              <Box component="tr">
                {["", "Setting", "Change", "Rollback"].map((h) => (
                  <Box
                    component="th"
                    key={h || "op"}
                    sx={{
                      ...cell,
                      typography: "s3",
                      color: "text.tertiary",
                      fontWeight: 500,
                      textAlign: "left",
                    }}
                  >
                    {h}
                  </Box>
                ))}
              </Box>
            </Box>
            <Box component="tbody">
              {plan.changes.map((c) => (
                <Box component="tr" key={c.path}>
                  <Box
                    component="td"
                    sx={{ ...cell, color: OP_COLOR[c.op], width: 70 }}
                  >
                    {OP_LABEL[c.op]}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      fontFamily: "ui-monospace, Menlo, monospace",
                      width: 210,
                    }}
                  >
                    {c.path}
                  </Box>
                  <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                    {c.what}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      ...cell,
                      color: c.danger ? "status.danger.fg" : "text.secondary",
                    }}
                  >
                    {c.rollback}
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>

          {risky && (
            <FormControlLabel
              sx={{ "& .MuiFormControlLabel-label": { typography: "s2" } }}
              control={
                <Checkbox
                  size="small"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                />
              }
              label="I understand one change can't be rolled back."
            />
          )}
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            pl: 2.5,
            pr: 9,
            py: 1.5,
            borderTop: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
          >
            {applied
              ? "Applied to staging. Promote it to production when it's checked."
              : `${plan.changes.length} changes. Same as fi config apply --profile ${plan.profile} --plan ${plan.id}.`}
          </Typography>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={(risky && !ack) || applied}
            onClick={() => setApplied(true)}
          >
            {applied ? "Applied" : "Apply to staging"}
          </Button>
        </Stack>
      </Box>
    </SideDrawer>
  );
}
ConfigPlanDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
