import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { envSnapshotsOf, currentEnvSnapshot } from "../_mock/envSnapshot";
import RollbackDialog from "./RollbackDialog";

const when = (at) =>
  at
    ? new Date(at).toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;

/*
  The header's "env vN ▾": the environment versions, newest first, the one
  runs use marked default, and Roll back on the others (the same dialog as
  History, so a roll back makes the next version and deletes nothing).
  History stays the full record, linked at the bottom.
*/
export default function EnvVersionMenu({
  env,
  envState,
  patch,
  readOnly,
  onHistory,
}) {
  const [anchor, setAnchor] = useState(null);
  const [rollbackTo, setRollbackTo] = useState(null);
  const current = currentEnvSnapshot(env, envState);
  /* One row per environment version: the newest snapshot holding it. */
  const versions = useMemo(() => {
    const byLabel = new Map();
    envSnapshotsOf(env, envState).forEach((s) => {
      if (s.envLabel) byLabel.set(s.envLabel, s);
    });
    return [...byLabel.values()].reverse();
  }, [env, envState]);
  if (!current?.envLabel) return null;
  const canSwitch = !readOnly && !!patch;
  return (
    <>
      <ButtonBase
        onClick={(e) => setAnchor(e.currentTarget)}
        aria-haspopup="dialog"
        sx={{
          px: 0.75,
          height: 22,
          gap: 0.5,
          borderRadius: "4px",
          border: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          color: "text.secondary",
          typography: "s3",
          fontWeight: "fontWeightMedium",
          "&:hover": { color: "text.primary", bgcolor: "action.hover" },
        }}
      >
        <Iconify icon="solar:code-linear" width={13} />
        {`env ${current.envLabel}`}
        <Iconify icon="solar:alt-arrow-down-linear" width={12} />
      </ButtonBase>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 380, mt: 0.5, maxHeight: 460 } } }}
      >
        <Box sx={{ px: 2, pt: 1.5, pb: 1 }}>
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            Environment versions
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
          >
            Runs use the default version. You can roll back to an earlier one.
          </Typography>
        </Box>
        <Stack sx={{ px: 1, pb: 1 }}>
          {versions.map((v) => {
            const isDefault = v.envLabel === current.envLabel;
            return (
              <Box
                key={v.envLabel}
                sx={{
                  px: 1,
                  py: 1,
                  borderRadius: "6px",
                  bgcolor: isDefault ? "action.hover" : "transparent",
                }}
              >
                <Stack direction="row" alignItems="center" spacing={0.75}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                  >
                    {v.envLabel}
                  </Typography>
                  {isDefault && (
                    <Box
                      component="span"
                      sx={{
                        typography: "s3",
                        px: 0.75,
                        lineHeight: "18px",
                        borderRadius: "4px",
                        bgcolor: "background.neutral",
                        border: "1px solid",
                        borderColor: "divider",
                        color: "text.secondary",
                      }}
                    >
                      default
                    </Box>
                  )}
                </Stack>
                {v.summary && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
                  >
                    {v.summary}
                  </Typography>
                )}
                {when(v.at) && (
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
                  >
                    {when(v.at)}
                  </Typography>
                )}
                {!isDefault && canSwitch && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      setAnchor(null);
                      setRollbackTo(v.label);
                    }}
                    sx={{
                      mt: 0.75,
                      color: "text.primary",
                      borderColor: "divider",
                    }}
                  >
                    {`Roll back to ${v.envLabel}`}
                  </Button>
                )}
              </Box>
            );
          })}
        </Stack>
        {onHistory && (
          <Box
            sx={{
              px: 2,
              py: 1,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <ButtonBase
              onClick={() => {
                setAnchor(null);
                onHistory();
              }}
              sx={{
                typography: "s3",
                color: "primary.main",
                fontWeight: "fontWeightMedium",
              }}
            >
              See full history
            </ButtonBase>
          </Box>
        )}
      </Popover>
      {rollbackTo && (
        <RollbackDialog
          open
          targetLabel={rollbackTo}
          env={env}
          envState={envState}
          patch={patch}
          onClose={() => setRollbackTo(null)}
        />
      )}
    </>
  );
}

EnvVersionMenu.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
  onHistory: PropTypes.func,
};
