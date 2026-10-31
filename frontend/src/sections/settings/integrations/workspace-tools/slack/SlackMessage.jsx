import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { clickable, focusRing } from "../clickable";

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
            borderRadius: 0.75,
            flexShrink: 0,
            bgcolor: "#7857FC",
            display: "grid",
            placeItems: "center",
          }}
        >
          <Typography sx={{ color: "#fff", fontWeight: 800, fontSize: 13 }}>
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
            <Typography sx={{ typography: "s2", fontWeight: 800 }}>
              Future AGI
            </Typography>
            <Box
              sx={{
                px: 0.5,
                borderRadius: 0.5,
                bgcolor: (t) => alpha(t.palette.text.primary, 0.1),
              }}
            >
              <Typography
                sx={{ fontSize: 10, fontWeight: 700, color: "text.secondary" }}
              >
                APP
              </Typography>
            </Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {msg.channel ? `${ago(msg.at)} · ${msg.channel}` : ago(msg.at)}
            </Typography>
          </Stack>
          <Typography sx={{ typography: "s2", fontWeight: 700, mt: 0.25 }}>
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
                    borderRadius: 0.75,
                    border: "1px solid",
                    cursor: onAction ? "pointer" : "default",
                    borderColor: "divider",
                    bgcolor: "transparent",
                    color: "text.primary",
                    "&:hover": onAction ? { opacity: 0.85 } : {},
                  }}
                >
                  <Typography
                    sx={{ typography: "s3", fontWeight: 700, color: "inherit" }}
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
