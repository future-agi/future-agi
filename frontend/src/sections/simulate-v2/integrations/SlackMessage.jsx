import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { clickable, focusRing } from "../components/clickable";

const ago = (iso) => {
  if (!iso) return "now";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

/**
 * A message as it lands in Slack — so the person setting up routing sees
 * exactly what their team will see. `onAction` makes the buttons live: each
 * one opens what the message is about.
 */
export default function SlackMessage({ msg, onAction }) {
  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1,
        // eslint-disable-next-line no-restricted-syntax -- ds: data colour
        bgcolor: (t) => (t.palette.mode === "dark" ? "#1A1D21" : "#FFFFFF"),
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={1.25}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: "4px",
            flexShrink: 0,
            bgcolor: "status.discovery.solid",
            display: "grid",
            placeItems: "center",
          }}
        >
          <Typography
            sx={{
              color: "status.discovery.onSolid",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            F
          </Typography>
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={0.75}
            flexWrap="wrap"
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Future AGI
            </Typography>
            <Box
              sx={{
                px: 0.5,
                borderRadius: "4px",
                bgcolor: (t) => alpha(t.palette.text.primary, 0.1),
              }}
            >
              <Typography
                sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary" }}
              >
                APP
              </Typography>
            </Box>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle" }}
            >{`${ago(msg.at)} · ${msg.channel}`}</Typography>
          </Stack>
          <Typography sx={{ typography: "s2", fontWeight: 600, mt: 0.25 }}>
            {msg.title}
          </Typography>
          {msg.lines.map((l) => (
            <Typography
              key={l}
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              {l}
            </Typography>
          ))}
          {msg.actions?.length > 0 && (
            <Stack
              direction="row"
              spacing={0.75}
              sx={{ mt: 1 }}
              flexWrap="wrap"
              rowGap={0.75}
            >
              {msg.actions.map((a) => (
                <Box
                  key={a}
                  {...clickable(onAction ? () => onAction(a) : undefined)}
                  sx={{
                    ...focusRing,
                    px: 1.25,
                    height: 28,
                    display: "inline-flex",
                    alignItems: "center",
                    borderRadius: "4px",
                    border: "1px solid",
                    cursor: onAction ? "pointer" : "default",
                    borderColor: "divider",
                    bgcolor: "transparent",
                    color: "text.primary",
                    "&:hover": onAction ? { opacity: 0.85 } : {},
                  }}
                >
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
                  >
                    {a}
                  </Typography>
                </Box>
              ))}
            </Stack>
          )}
        </Box>
      </Stack>
    </Box>
  );
}

SlackMessage.propTypes = {
  msg: PropTypes.object.isRequired,
  onAction: PropTypes.func,
};
