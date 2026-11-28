import { useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import SideDrawer from "src/sections/simulate/environments/components/SideDrawer";
import { CONNECTED_AGENTS_PATH } from "../agentsTabs";
import ConnectSection from "src/sections/simulate/environments/buildDrawer/ConnectSection";
import BrandLogoTile from "src/sections/simulate/environments/buildDrawer/LogoTile";
import GitHubConnectFlow from "./GitHubConnectFlow";
import PlatformConnectFlow from "./PlatformConnectFlow";
import SoonFrameworks from "./SoonFrameworks";
import UploadConnectFlow from "./UploadConnectFlow";
import UploadDropZone from "./UploadDropZone";
import {
  AGENT_CONNECT_ITEMS,
  GITHUB_SOURCE,
  PLATFORM_SOURCES,
  SOON_FRAMEWORKS,
  UPLOAD_SOURCE,
  connectItemOf,
} from "./connectSources";
import {
  clearConnectDraft,
  hasConnectDraft,
  saveConnectDraft,
} from "./connectDraft";

export default function ConnectAgentPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const source = params.get("source");
  const onGitHub = source === GITHUB_SOURCE;
  const onUpload = source === UPLOAD_SOURCE;
  const onPlatform = PLATFORM_SOURCES.includes(source);
  const drawerOpen = onGitHub || onUpload || onPlatform;
  const opened = drawerOpen ? connectItemOf(source) : null;
  const [confirmClose, setConfirmClose] = useState(false);
  const close = () => {
    if (hasConnectDraft(source)) {
      setConfirmClose(true);
      return;
    }
    setParams({});
  };
  // A folder dropped on the page is already uploaded and being read, so the
  // drawer opens on the agents it found, not on picking a folder again.
  const openScanned = ({ folder, scanId }) => {
    saveConnectDraft({ stage: "agents", folder, scanId }, UPLOAD_SOURCE);
    setParams({ source: UPLOAD_SOURCE });
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
      <PageHeader
        back={
          <IconButton
            size="small"
            aria-label="Back to agents"
            onClick={() => navigate(CONNECTED_AGENTS_PATH)}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={20} />
          </IconButton>
        }
        title="New agent"
        description="Connect it from where it runs today."
      />
      <ConnectSection
        head={false}
        items={AGENT_CONNECT_ITEMS}
        onPick={(item) => setParams({ source: item.id })}
        sdkSlot={<SoonFrameworks items={SOON_FRAMEWORKS} />}
        uploadSlot={
          <UploadDropZone onScanned={openScanned} disabled={drawerOpen} />
        }
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
              <BrandLogoTile
                logo={opened?.logo}
                icon={opened?.icon}
                size={32}
              />
              <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
                {onUpload ? "Upload a folder" : opened?.name}
              </Typography>
            </Stack>
            <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              {onPlatform ? (
                <PlatformConnectFlow key={source} platform={source} />
              ) : onUpload ? (
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
