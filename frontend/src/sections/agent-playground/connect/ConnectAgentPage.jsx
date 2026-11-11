import { useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import Iconify from "src/components/iconify";
import SideDrawer from "src/sections/simulate/environments/components/SideDrawer";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import ConnectCatalog from "./ConnectCatalog";
import GitHubConnectFlow from "./GitHubConnectFlow";
import UploadConnectFlow from "./UploadConnectFlow";
import { LogoTile } from "./connectBits";
import { GITHUB_SOURCE, UPLOAD_SOURCE } from "./connectSources";
import { clearConnectDraft, hasConnectDraft } from "./connectDraft";

export default function ConnectAgentPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const source = params.get("source");
  const onGitHub = source === GITHUB_SOURCE;
  const onUpload = source === UPLOAD_SOURCE;
  const drawerOpen = onGitHub || onUpload;
  const [confirmClose, setConfirmClose] = useState(false);
  const close = () => {
    if (hasConnectDraft(source)) {
      setConfirmClose(true);
      return;
    }
    setParams({});
  };
  const discard = () => {
    clearConnectDraft(source);
    setConfirmClose(false);
    setParams({});
  };

  return (
    <Box sx={{ height: "100%", overflow: "auto", px: 3, pt: 2, pb: 4 }}>
      <Helmet>
        <title>New agent · Agents</title>
      </Helmet>
      <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ mb: 3 }}>
        <IconButton
          size="small"
          aria-label="Back to agents"
          onClick={() => navigate(CONNECTED_AGENTS_PATH)}
          sx={{ mt: 0.25 }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={20} />
        </IconButton>
        <Box>
          <Typography sx={{ typography: "m2", fontWeight: 600 }}>
            New agent
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Connect it from where it runs today.
          </Typography>
        </Box>
      </Stack>
      <ConnectCatalog
        pickedId={source}
        onPick={(item) => setParams({ source: item.id })}
      />
      <SideDrawer open={drawerOpen} onClose={close} width={960}>
        {drawerOpen && (
          <Box
            sx={{
              height: "100%",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2.5,
                py: 1.75,
                pr: 6,
                borderBottom: "1px solid",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              <LogoTile
                icon={
                  onUpload
                    ? "solar:upload-minimalistic-linear"
                    : "simple-icons:github"
                }
                size={32}
              />
              <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
                {onUpload ? "Upload a folder" : "GitHub"}
              </Typography>
            </Stack>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              {onUpload ? (
                <UploadConnectFlow />
              ) : (
                <GitHubConnectFlow
                  outcome={params.get("github") || undefined}
                />
              )}
            </Box>
          </Box>
        )}
      </SideDrawer>
      <ConfirmDialog
        open={confirmClose}
        onClose={() => setConfirmClose(false)}
        title="Discard this agent?"
        content={
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            The scan results and anything you entered will be discarded.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={discard}
          >
            Discard
          </Button>
        }
      />
    </Box>
  );
}
