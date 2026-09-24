import { Box, Button, Divider, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../components/SectionCard";
import CopyField from "../components/CopyField";
import {
  LOCAL_CARD,
  LOCAL_INSTALL_HINT,
  TEMPLATE_SHAPE,
  localScaffoldSteps,
} from "../useTemplate.constants";

/**
 * The "develop locally" card — three CLI steps that scaffold this template
 * into the user's own repo, each with a copyable `fai …` command.
 */
export default function LocalScaffoldCard({ template }) {
  const steps = localScaffoldSteps(template);

  return (
    <SectionCard title={LOCAL_CARD.title} subtitle={LOCAL_CARD.subtitle}>
      <Stack sx={{ p: 2.5 }} spacing={0}>
        {steps.map((step, i) => (
          <Stack key={step.n} direction="row" spacing={1.75}>
            <Stack alignItems="center" sx={{ flexShrink: 0 }}>
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
                  border: "1px solid", borderColor: "divider",
                  typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary",
                }}
              >
                {step.n}
              </Box>
              {i < steps.length - 1 && (
                <Box sx={{ flex: 1, width: "1px", bgcolor: "divider", my: 0.75, minHeight: 24 }} />
              )}
            </Stack>
            <Box sx={{ flex: 1, minWidth: 0, pb: i < steps.length - 1 ? 2.25 : 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
                {step.title}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1 }}>
                {step.body}
              </Typography>
              <CopyField value={step.cmd} wrap />
            </Box>
          </Stack>
        ))}
      </Stack>
      <Divider />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2.5, py: 1.75 }}>
        <Iconify icon="solar:book-linear" width={14} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          Not installed?{" "}
          <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>
            {LOCAL_INSTALL_HINT}
          </Box>
        </Typography>
        {/* TODO: point at the CLI docs URL once it exists. */}
        <Button size="small" disabled sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.secondary" }}>
          Docs
        </Button>
      </Stack>
    </SectionCard>
  );
}
LocalScaffoldCard.propTypes = { template: TEMPLATE_SHAPE };
