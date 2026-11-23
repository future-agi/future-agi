import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REVIEW_COPY } from "./designReview.constants";

export function ReviewBanner({ text, cta, onClick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        mt: 2,
        px: 1.5,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "status.warning.border",
        bgcolor: "status.warning.bg",
      }}
    >
      <Iconify
        icon="solar:danger-triangle-linear"
        width={16}
        sx={{ color: "status.warning.fg", flexShrink: 0 }}
      />
      <Typography sx={{ typography: "s1", color: "text.primary", flex: 1 }}>
        {text}
      </Typography>
      {cta && onClick && (
        <Button
          size="small"
          variant="outlined"
          onClick={onClick}
          sx={{
            color: "text.primary",
            borderColor: "divider",
            bgcolor: "background.paper",
            flexShrink: 0,
          }}
        >
          {cta}
        </Button>
      )}
    </Stack>
  );
}

ReviewBanner.propTypes = {
  text: PropTypes.string.isRequired,
  cta: PropTypes.string,
  onClick: PropTypes.func,
};

export default function ReviewBrief({ brief }) {
  const agent = brief?.agent;
  const rows = [
    [
      REVIEW_COPY.briefRows.description,
      brief?.description || REVIEW_COPY.descriptionFallback,
    ],
    [REVIEW_COPY.briefRows.sources, REVIEW_COPY.sourcesValue],
    [
      REVIEW_COPY.briefRows.channel,
      REVIEW_COPY.channel[brief?.channel] || REVIEW_COPY.channel.chat,
    ],
    [
      REVIEW_COPY.briefRows.agent,
      agent
        ? [agent.name, agent.version].filter(Boolean).join(" · ")
        : REVIEW_COPY.noAgent,
    ],
  ];
  return (
    <Box
      sx={{
        mt: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          height: 36,
          px: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
          {REVIEW_COPY.brief}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {REVIEW_COPY.briefSubtitle}
        </Typography>
      </Stack>
      <Box
        component="table"
        sx={{ width: 1, borderCollapse: "collapse", my: 0.5 }}
      >
        <Box component="tbody">
          {rows.map(([key, value]) => (
            <Box component="tr" key={key}>
              <Box
                component="td"
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 120,
                  px: 1.5,
                  py: 0.75,
                  verticalAlign: "top",
                }}
              >
                {key}
              </Box>
              <Box
                component="td"
                sx={{
                  typography: "s1",
                  color: "text.primary",
                  px: 1.5,
                  py: 0.75,
                }}
              >
                {value}
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  );
}

ReviewBrief.propTypes = {
  brief: PropTypes.shape({
    description: PropTypes.string,
    channel: PropTypes.string,
    agent: PropTypes.shape({
      name: PropTypes.string,
      version: PropTypes.string,
    }),
  }),
};
