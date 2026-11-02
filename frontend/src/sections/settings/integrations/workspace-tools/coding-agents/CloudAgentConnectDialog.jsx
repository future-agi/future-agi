import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  IMPROVER_TOOLS,
  cloudKeySlotOf,
  connectCloudAgent,
  improverTokenLabel,
  useIntegrations,
} from "../store";
import { keyRefOf } from "./secretRefs";
import SecretRefField from "./SecretRefField";
import BrandMark from "../BrandMark";
import { Returning } from "../ExternalPage";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Connecting a cloud coding agent — once, for the workspace. Its vendor key
 * is recorded as a secret-manager reference only; the key stays in the
 * secret manager and is resolved when a dispatch goes out.
 *   setup    → the key's reference, and what a dispatch hands over
 *   checking → the reference resolves and the vendor accepts the key
 *   done     → connected, with its own scoped improver token; Copy for coding
 *              agent offers Dispatch to … for everyone in the workspace
 */
export default function CloudAgentConnectDialog({ open, onClose, agent }) {
  const [phase, setPhase] = useState("setup");
  const [ref, setRef] = useState("");
  const { cloudAgents = {} } = useIntegrations();
  const slot = agent ? cloudKeySlotOf(agent.id) : "";
  const keyRef = keyRefOf(ref, slot);

  useEffect(() => {
    if (!open || !agent) return;
    setPhase("setup");
    setRef(cloudAgents[agent.id]?.keyRef || "");
  }, [open, agent]); // eslint-disable-line react-hooks/exhaustive-deps

  /* In the product this resolves the reference and makes one call to the
     vendor's API with it. */
  useEffect(() => {
    if (phase !== "checking") return undefined;
    const t = setTimeout(() => {
      connectCloudAgent(agent.id, { keyRef });
      setPhase("done");
    }, 1100);
    return () => clearTimeout(t);
  }, [phase, agent, keyRef]);

  if (!agent) return null;
  const conn = cloudAgents[agent.id];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 3, pt: 2.5, pb: 2 }}
      >
        <BrandMark brand={agent.id} size={36} />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "m3", fontWeight: 700 }}
          >{`Connect ${agent.label}`}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            For the whole workspace. It works in its own sandbox. Future AGI
            checks every change it submits.
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={18} />
        </IconButton>
      </Stack>

      <Box sx={{ px: 3, pb: 2.5 }}>
        {phase === "setup" && (
          <Stack spacing={2.25}>
            <Step n={1} title={`Reference your ${agent.vendor} API key`}>
              <SecretRefField
                value={ref}
                onChange={setRef}
                placeholder={slot}
              />
            </Step>
            <Step n={2} title="What each dispatch hands over">
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                {[
                  [
                    "The brief",
                    "The issue, root cause, evidence, visible failing scenarios and constraints",
                  ],
                  [
                    "The repository",
                    "The agent's own, from its Connections, only ones the GitHub App can see",
                  ],
                  [
                    "An improver token",
                    `Scoped to ${IMPROVER_TOOLS.map((t) => t.name).join(", ")}. Held-back scenarios stay hidden`,
                  ],
                ].map(([k, v], i) => (
                  <Stack
                    key={k}
                    direction="row"
                    spacing={1.5}
                    sx={{
                      px: 1.5,
                      py: 1,
                      borderTop: i ? "1px solid" : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: 600,
                        width: 130,
                        flexShrink: 0,
                      }}
                    >
                      {k}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {v}
                    </Typography>
                  </Stack>
                ))}
              </Box>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
              >
                Its change comes back through submit_candidate. Future AGI runs
                it next to today&apos;s version and computes the verdict. The
                agent never grades its own work.
              </Typography>
            </Step>
          </Stack>
        )}

        {phase === "checking" && (
          <Returning label={`Checking the key with ${agent.vendor}…`} />
        )}

        {phase === "done" && (
          <Stack spacing={1.5}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Iconify
                icon="solar:check-circle-bold"
                width={16}
                sx={{ color: "text.primary", flexShrink: 0 }}
              />
              <Typography
                sx={{ typography: "s2", fontWeight: 600, flex: 1 }}
              >{`${agent.label} connected`}</Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                for the workspace · by you
              </Typography>
            </Stack>
            {conn && (
              <Stack spacing={0.5} sx={{ px: 1.5 }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Iconify
                    icon="solar:lock-keyhole-minimalistic-linear"
                    width={14}
                    sx={{ color: "text.subtitle", flexShrink: 0 }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s3",
                      fontFamily: MONO,
                      color: "text.secondary",
                    }}
                  >
                    {conn.keyRef}
                  </Typography>
                </Stack>
                {conn.token && (
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Iconify
                      icon="solar:key-minimalistic-linear"
                      width={14}
                      sx={{ color: "text.subtitle", flexShrink: 0 }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      <Box
                        component="span"
                        sx={{ fontFamily: MONO, color: "text.secondary" }}
                      >
                        {improverTokenLabel(conn.token)}
                      </Box>
                      {" · scoped · held-back scenarios hidden"}
                    </Typography>
                  </Stack>
                )}
              </Stack>
            )}
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {`Copy for coding agent now offers Dispatch to ${agent.label} on Error Feed issues, run diagnoses and improvements, for everyone in the workspace.`}
            </Typography>
          </Stack>
        )}
      </Box>

      {phase !== "checking" && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {phase === "setup" && (
            <>
              <Button
                size="small"
                onClick={onClose}
                sx={{ color: "text.secondary" }}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!keyRef}
                onClick={() => setPhase("checking")}
              >
                {conn ? "Save" : "Connect"}
              </Button>
            </>
          )}
          {phase === "done" && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onClose}
            >
              Done
            </Button>
          )}
        </Stack>
      )}
    </Dialog>
  );
}

CloudAgentConnectDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  agent: PropTypes.object,
};

function Step({ n, title, children }) {
  return (
    <Stack direction="row" spacing={1.25} alignItems="flex-start">
      <Box
        sx={{
          width: 18,
          height: 18,
          mt: 0.125,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
          border: "1px solid",
          borderColor: "text.primary",
        }}
      >
        <Typography sx={{ fontSize: 10, fontWeight: 700 }}>{n}</Typography>
      </Box>
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700, mb: 0.75 }}>
          {title}
        </Typography>
        {children}
      </Box>
    </Stack>
  );
}
Step.propTypes = {
  n: PropTypes.number,
  title: PropTypes.string,
  children: PropTypes.node,
};
