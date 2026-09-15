import { useParams, useNavigate } from "react-router-dom";
import { Box, Stack, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { EmptyState } from "../components/primitives";
import TemplateBuildPanel from "./TemplateBuildPanel";

/**
 * Standalone "use this template" page — a deep-link / direct entry to a single
 * template's build panel. The templates browser embeds the same
 * `TemplateBuildPanel` inline as its detail pane, so both entries share one
 * flow: choose where to build (here / locally), no agent-connection step.
 */
export default function UseTemplate() {
  const { templateId } = useParams();
  const navigate = useNavigate();
  const template = getEnvironment(templateId);

  if (!template) {
    return (
      <Box sx={{ p: 3 }}>
        <EmptyState icon="solar:danger-triangle-linear" title="Template not found" body="It may have been renamed." />
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Tooltip arrow title="All templates">
          <Button
            onClick={() => navigate(paths.dashboard.simulate.environmentTemplates)}
            sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle", flexShrink: 0 }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </Button>
        </Tooltip>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <Box sx={{ maxWidth: 560 }}>
          <TemplateBuildPanel template={template} showName />
        </Box>
      </Box>
    </Box>
  );
}
