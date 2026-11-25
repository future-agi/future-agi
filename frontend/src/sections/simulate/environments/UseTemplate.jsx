import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { usePrebuiltEnvironments } from "src/api/simulate-environments/prebuilt";
import TemplateBuildPanel from "./cards/TemplateBuildPanel";
import { TEMPLATE_NOT_FOUND_COPY } from "./useTemplate.constants";

/**
 * The prebuilt-template selection page — the screen shown after a template
 * card is clicked. Resolves the `:templateId` route param against the template
 * library and renders the "where to build it" panel for it.
 */
export default function UseTemplate() {
  const navigate = useNavigate();
  const { templateId } = useParams();
  const { data, isLoading } = usePrebuiltEnvironments();

  const template = useMemo(
    () => (data ?? []).find((t) => t.id === templateId),
    [data, templateId],
  );

  const backToTemplates = () =>
    navigate(paths.dashboard.simulate.environments.templates);

  return (
    <Box sx={{ p: 2, maxWidth: 640, mx: "auto" }}>
      <Box sx={{ mb: 2 }}>
        <CustomTooltip show arrow size="small" title="Back to templates">
          <IconButton
            aria-label="Back to templates"
            size="small"
            onClick={backToTemplates}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </IconButton>
        </CustomTooltip>
      </Box>

      {template ? (
        <TemplateBuildPanel template={template} showName />
      ) : (
        !isLoading && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", py: 6, textAlign: "center" }}>
            {TEMPLATE_NOT_FOUND_COPY}
          </Typography>
        )
      )}
    </Box>
  );
}
