import PropTypes from "prop-types";
import { useState } from "react";
import { useSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import {
  Drawer, Box, Stack, Typography, IconButton, Button, Divider, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import TwinLogo from "../components/TwinLogo";
import { twinById } from "../_mock/twins";

const TWIN_TINT = "#7857FC";
const SUCCESS = "#16A34A";
const DANGER = "#C2603F";

/**
 * Sandbox controls — the right-slide drawer opened by the "Controls"
 * button on the Twin detail page. This is the fast-access surface
 * for the operations a user thinks about while looking at the twin:
 *
 *   · Reset the sandbox now (with confirm)
 *   · Rotate service credentials
 *   · Copy each service's sandbox endpoint
 *   · View the resolved seed JSON
 *   · Deep-link into the full env Settings for anything more advanced
 *
 * Deliberately narrower than the full TwinConfigSection on the env
 * Settings page — same actions, but tuned for "I'm on the twin
 * detail and want to do X right now" rather than "I'm auditing all
 * of this env's config".
 */
export default function TwinControlsDrawer({ open, onClose, envId, backing, patch }) {
  const { enqueueSnackbar } = useSnackbar();
  const [confirmReset, setConfirmReset] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [showJson, setShowJson] = useState(false);

  if (!backing) return null;

  const resetNow = () => {
    patch({
      twinBacking: {
        ...backing,
        provisionedAt: new Date().toISOString(),
        activity: Object.fromEntries((backing.services || []).map((sId) => [sId, { requests: 0, failures: 0 }])),
      },
    });
    setConfirmReset(false);
    enqueueSnackbar("Sandbox reset · fresh seed installed", { variant: "success" });
  };

  const rotateCreds = () => {
    setRotating(true);
    setTimeout(() => {
      setRotating(false);
      enqueueSnackbar(
        `Credentials rotated for ${backing.services.length} service${backing.services.length === 1 ? "" : "s"}`,
        { variant: "success" },
      );
    }, 900);
  };

  const copyEndpoint = (url) => {
    navigator.clipboard?.writeText(url);
    enqueueSnackbar("Endpoint copied", { variant: "info" });
  };

  return (
    <Drawer
      anchor="right" open={open} onClose={onClose}
      PaperProps={{
        sx: {
          width: 420, bgcolor: "background.paper", backgroundImage: "none",
          borderLeft: "1px solid", borderColor: "divider",
        },
      }}
    >
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{ px: 2, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box sx={{
            width: 26, height: 26, borderRadius: 0.75,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(TWIN_TINT, t.palette.mode === "dark" ? 0.18 : 0.1),
            color: TWIN_TINT,
          }}>
            <Iconify icon="solar:settings-linear" width={14} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>Sandbox controls</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Actions that operate on the clone sandbox
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, overflow: "auto", px: 2, py: 2 }}>
          <Stack spacing={2.5}>
            {/* status */}
            <StatusRow backing={backing} />

            {/* endpoints */}
            <Section title="Sandbox endpoints" subtitle="Where your agent calls in for each service">
              <Stack spacing={0.75}>
                {(backing.services || []).map((sId) => {
                  const t = twinById(sId);
                  const url = backing.endpoints?.[sId] || "—";
                  return (
                    <Stack key={sId} direction="row" alignItems="center" spacing={1.25}
                      sx={{
                        p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider",
                      }}
                    >
                      <TwinLogo twin={t} width={16} />
                      <Box flex={1} minWidth={0}>
                        <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{t?.name || sId}</Typography>
                        <Typography noWrap sx={{
                          typography: "s3", color: "text.subtitle",
                          fontFamily: "ui-monospace, Menlo, monospace",
                        }}>
                          {url}
                        </Typography>
                      </Box>
                      <Tooltip title="Copy endpoint">
                        <IconButton size="small" onClick={() => copyEndpoint(url)}>
                          <Iconify icon="solar:copy-linear" width={13} sx={{ color: "text.subtitle" }} />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  );
                })}
              </Stack>
            </Section>

            {/* actions */}
            <Section title="Operations" subtitle="Everything here affects the next run against this env">
              <Stack spacing={0.75}>
                <ActionButton
                  icon="solar:key-linear"
                  label={rotating ? "Rotating…" : "Rotate service credentials"}
                  onClick={rotateCreds}
                  disabled={rotating}
                  spinning={rotating}
                />
                {!confirmReset ? (
                  <ActionButton
                    icon="solar:refresh-circle-linear"
                    label="Reset sandbox now"
                    onClick={() => setConfirmReset(true)}
                    danger
                  />
                ) : (
                  <Box sx={{
                    p: 1.25, borderRadius: 1,
                    border: "1px solid", borderColor: alpha(DANGER, 0.4),
                    bgcolor: (t) => alpha(DANGER, t.palette.mode === "dark" ? 0.1 : 0.05),
                  }}>
                    <Typography sx={{ typography: "s2", fontWeight: 700, color: DANGER, mb: 0.5 }}>
                      Reset the sandbox?
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                      Current state is discarded and re-provisioned from the seed. In-flight runs will fail. Past runs stay in history.
                    </Typography>
                    <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }}>
                      <Button
                        variant="contained" size="small"
                        onClick={resetNow}
                        sx={{
                          typography: "s2", fontWeight: 700, bgcolor: DANGER, color: "common.white",
                          "&:hover": { bgcolor: "#A54E32" },
                        }}
                      >
                        Reset
                      </Button>
                      <Button
                        size="small" onClick={() => setConfirmReset(false)}
                        sx={{ typography: "s2", fontWeight: 700, color: "text.secondary" }}
                      >
                        Cancel
                      </Button>
                    </Stack>
                  </Box>
                )}
              </Stack>
            </Section>

            {/* seed */}
            <Section title="Seed" subtitle="Natural-language prompt that shapes each fresh sandbox">
              <Box sx={{
                p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider",
                bgcolor: "background.neutral",
              }}>
                <Typography sx={{
                  typography: "s2", color: backing.seedPrompt ? "text.primary" : "text.subtitle",
                  fontStyle: backing.seedPrompt ? "normal" : "italic",
                }}>
                  {backing.seedPrompt || "No seed prompt — the sandbox starts empty."}
                </Typography>
              </Box>
              <Button
                size="small" onClick={() => setShowJson((v) => !v)}
                startIcon={<Iconify icon={showJson ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
                sx={{
                  typography: "s3", fontWeight: 700, color: "text.secondary",
                  justifyContent: "flex-start", px: 0, mt: 0.75,
                }}
              >
                {showJson ? "Hide resolved JSON" : "View resolved JSON"}
              </Button>
              {showJson && (
                <Box sx={{
                  mt: 0.75, p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider",
                  bgcolor: "background.neutral", maxHeight: 220, overflow: "auto",
                }}>
                  <Typography component="pre" sx={{
                    typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
                    color: "text.primary", whiteSpace: "pre", m: 0,
                  }}>
                    {backing.seed || "{}"}
                  </Typography>
                </Box>
              )}
            </Section>
          </Stack>
        </Box>

        <Divider />
        <Box sx={{ px: 2, py: 1.5, flexShrink: 0 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Advanced config (versioning, seed edit, credentials map) lives on the env&apos;s{" "}
            <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>
              Settings tab
            </Box>
            .
          </Typography>
        </Box>
      </Stack>
    </Drawer>
  );
}

TwinControlsDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  envId: PropTypes.string,
  backing: PropTypes.object,
  patch: PropTypes.func,
};

/* ── bits ────────────────────────────────────────────────────────────── */

function StatusRow({ backing }) {
  const provisioned = backing.provisionedAt
    ? timeAgo(backing.provisionedAt)
    : "—";
  return (
    <Stack direction="row" alignItems="center" spacing={1}
      sx={{
        p: 1.25, borderRadius: 1,
        border: "1px solid", borderColor: alpha(SUCCESS, 0.35),
        bgcolor: (t) => alpha(SUCCESS, t.palette.mode === "dark" ? 0.08 : 0.04),
      }}
    >
      <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: SUCCESS }} />
      <Typography sx={{ typography: "s2", fontWeight: 700, color: SUCCESS }}>
        Serving
      </Typography>
      <Box flex={1} />
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        Provisioned {provisioned}
      </Typography>
    </Stack>
  );
}
StatusRow.propTypes = { backing: PropTypes.object };

function Section({ title, subtitle, children }) {
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
      {subtitle && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.75 }}>
          {subtitle}
        </Typography>
      )}
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, subtitle: PropTypes.string, children: PropTypes.node };

function ActionButton({ icon, label, onClick, disabled, danger, spinning }) {
  const color = danger ? DANGER : "text.primary";
  return (
    <Button
      variant="outlined" fullWidth size="small"
      disabled={disabled}
      onClick={onClick}
      startIcon={
        spinning
          ? <Iconify icon="solar:refresh-circle-linear" width={13} sx={{ animation: "spin 1.2s linear infinite", "@keyframes spin": { to: { transform: "rotate(360deg)" } } }} />
          : <Iconify icon={icon} width={13} />
      }
      sx={{
        typography: "s2", fontWeight: 700,
        justifyContent: "flex-start",
        color,
        borderColor: (t) => danger ? alpha(DANGER, t.palette.mode === "dark" ? 0.5 : 0.4) : t.palette.divider,
        "&:hover": {
          borderColor: danger ? DANGER : "text.primary",
          bgcolor: (t) => danger
            ? alpha(DANGER, t.palette.mode === "dark" ? 0.1 : 0.06)
            : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
        },
      }}
    >
      {label}
    </Button>
  );
}
ActionButton.propTypes = {
  icon: PropTypes.string, label: PropTypes.string,
  onClick: PropTypes.func, disabled: PropTypes.bool,
  danger: PropTypes.bool, spinning: PropTypes.bool,
};

function timeAgo(iso) {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
