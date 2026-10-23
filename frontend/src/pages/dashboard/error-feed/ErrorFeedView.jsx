import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useOrganization } from "src/contexts/OrganizationContext";
import EscalationSettingsDialog from "./components/EscalationSettingsDialog";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useErrorFeedList } from "src/api/errorFeed/error-feed";
import ErrorFeedFilters from "./components/ErrorFeedFilters";
import ErrorFeedTable from "./components/ErrorFeedTable";
import { useErrorFeedApiParams } from "./store";

export default function ErrorFeedView() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { currentWorkspaceId } = useWorkspace();
  const { currentOrganizationId } = useOrganization();
  useEffect(() => {
    setSettingsOpen(false);
  }, [currentWorkspaceId, currentOrganizationId]);

  const apiParams = useErrorFeedApiParams();
  const { data } = useErrorFeedList(apiParams);
  const totalCount = data?.total ?? 0;

  const handleSelect = (clusterId, checked) => {
    setSelected((prev) =>
      checked ? [...prev, clusterId] : prev.filter((id) => id !== clusterId),
    );
  };

  const handleSelectAll = (checked, ids) => {
    setSelected(checked ? ids : []);
  };

  const handleClearSelection = () => setSelected([]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Page Header ── */}
      <Box
        sx={{
          px: 2,
          pt: 2,
          pb: 1.5,
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack gap={0.25}>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography
              color="text.primary"
              typography="m2"
              fontWeight="fontWeightSemiBold"
            >
              Error Feed
            </Typography>
            <Box
              sx={{
                px: 0.75,
                py: 0.25,
                borderRadius: "4px",
                bgcolor: "action.hover",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "text.secondary",
                  fontFeatureSettings: "'tnum'",
                }}
              >
                {totalCount}
              </Typography>
            </Box>
          </Stack>
          <Typography
            typography="s2"
            color="text.secondary"
            fontWeight="fontWeightRegular"
          >
            Track, triage, and resolve AI errors — hallucinations, eval
            failures, and pipeline issues
          </Typography>
        </Stack>

        <Stack direction="row" alignItems="center" gap={1}>
          <Tooltip title="Escalation settings" arrow>
            <span>
              <IconButton
                aria-label="Escalation settings"
                size="small"
                disabled={!currentWorkspaceId || !currentOrganizationId}
                onClick={() => setSettingsOpen(true)}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "6px",
                  width: 32,
                  height: 32,
                  color: "text.secondary",
                }}
              >
                <Iconify icon="solar:settings-linear" width={16} />
              </IconButton>
            </span>
          </Tooltip>
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor
                src="/assets/icons/navbar/ic_alert.svg"
                sx={{ width: 16, height: 16 }}
              />
            }
            onClick={() => navigate("/dashboard/error-feed/alerts")}
            sx={{
              height: 32,
              fontSize: "13px",
              borderColor: "divider",
              color: "text.secondary",
              borderRadius: "6px",
            }}
          >
            Alert rules
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor
                src="/assets/icons/ic_docs_single.svg"
                sx={{ width: 15, height: 15 }}
              />
            }
            component="a"
            href="https://docs.futureagi.com/docs/error-feed"
            target="_blank"
            sx={{
              height: 32,
              fontSize: "13px",
              borderColor: "divider",
              color: "text.secondary",
              borderRadius: "6px",
              "&:hover": { borderColor: "border.hover" },
            }}
          >
            Docs
          </Button>
        </Stack>
      </Box>

      {settingsOpen && (
        <EscalationSettingsDialog
          key={`${currentOrganizationId}:${currentWorkspaceId}`}
          open
          organizationId={currentOrganizationId}
          workspaceId={currentWorkspaceId}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {/* ── Content ── */}
      <Box
        sx={{
          flex: 1,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          p: 2,
        }}
      >
        {/* Filters */}
        <ErrorFeedFilters
          selected={selected}
          onClearSelection={handleClearSelection}
        />

        {/* Table */}
        <ErrorFeedTable
          selected={selected}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
        />
      </Box>
    </Box>
  );
}
