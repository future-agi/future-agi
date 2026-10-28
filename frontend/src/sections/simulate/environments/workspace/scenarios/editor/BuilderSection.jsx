import PropTypes from "prop-types";
import { Box, Button, ButtonBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { EDITOR_COPY } from "../scenarioEditor.constants";
import SectionHeader from "./SectionHeader";

// A field pinned to the verification proof: its value, and a suggested change
// request that goes to the builder composer on click.
function LockedRow({ label, value, prompt, action, onSend }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Iconify
          icon="solar:lock-keyhole-minimalistic-linear"
          width={14}
          sx={{ color: "text.subtitle", mt: "3px", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.secondary",
              mb: 0.375,
            }}
          >
            {label}
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              color: value ? "text.primary" : "text.disabled",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {value || "Not set"}
          </Typography>
          <CustomTooltip
            show
            arrow
            size="small"
            placement="top"
            title="Send this to the builder chat"
          >
            <ButtonBase
              aria-label={`Ask the builder to ${action}`}
              onClick={() => onSend(prompt)}
              sx={{
                mt: 1,
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                px: 1,
                py: 0.5,
                maxWidth: "100%",
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                color: "text.secondary",
                bgcolor: "background.paper",
                transition: "border-color .12s ease, color .12s ease",
                "&:hover, &.Mui-focusVisible": {
                  borderColor: "text.primary",
                  color: "text.primary",
                },
              }}
            >
              <Iconify
                icon="solar:chat-round-line-linear"
                width={12}
                sx={{ flexShrink: 0, opacity: 0.7 }}
              />
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  color: "inherit",
                  minWidth: 0,
                }}
              >
                {prompt}
              </Typography>
              <Iconify
                icon="solar:arrow-right-linear"
                width={12}
                sx={{ flexShrink: 0, opacity: 0.6 }}
              />
            </ButtonBase>
          </CustomTooltip>
        </Box>
      </Stack>
    </Box>
  );
}
LockedRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  prompt: PropTypes.string,
  action: PropTypes.string,
  onSend: PropTypes.func,
};

// What only the builder can change: the caller's instruction and the checks.
// Each row sends a change request; Send to builder sends the whole scenario.
export default function BuilderSection({
  name,
  instruction,
  checks,
  onSend,
  onSendScenario,
}) {
  return (
    <>
      <SectionHeader
        title={EDITOR_COPY.builderSection}
        hint={EDITOR_COPY.builderHint}
      />
      <LockedRow
        label="What the caller wants"
        value={instruction}
        action="change what the caller wants"
        prompt={`Change what the caller wants in ${name} to …`}
        onSend={onSend}
      />
      <LockedRow
        label="What we measure (checks)"
        value={checks}
        action="change the checks"
        prompt={`Change the checks on ${name} to …`}
        onSend={onSend}
      />
      <Stack
        direction="row"
        spacing={1.25}
        alignItems="center"
        sx={{
          p: 1.75,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:shield-check-linear"
          width={16}
          sx={{ color: "primary.main", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1 }}>
          {EDITOR_COPY.builderNote}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={onSendScenario}
          startIcon={<Iconify icon="solar:chat-round-line-linear" width={16} />}
          sx={{
            color: "text.primary",
            borderColor: "divider",
            flexShrink: 0,
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
          }}
        >
          {EDITOR_COPY.sendToBuilder}
        </Button>
      </Stack>
    </>
  );
}
BuilderSection.propTypes = {
  name: PropTypes.string,
  instruction: PropTypes.string,
  checks: PropTypes.string,
  onSend: PropTypes.func.isRequired,
  onSendScenario: PropTypes.func.isRequired,
};
