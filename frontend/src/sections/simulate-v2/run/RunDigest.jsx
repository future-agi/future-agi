import PropTypes from "prop-types";
import { SHOW_INSIGHTS } from "../_mock/featureFlags";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import { Box, Link, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SlackMessage from "../integrations/SlackMessage";

const colHead = {
  typography: "s3",
  fontWeight: 600,
  color: "text.tertiary",
  textTransform: "none",
  letterSpacing: 0.4,
};

/**
 * "Digest sent to #channel and 2 people" — the one message this run sent on
 * completion (PRD v6 · AC-20.8). Nothing per scenario. The preview renders
 * the digest exactly as it went out, in Slack and by email.
 */
export default function DigestLine({ digest, destinations, sentAt }) {
  const navigate = useNavigate();
  const navigateTo = (href) => navigate(href);
  const [anchor, setAnchor] = useState(null);
  if (!digest || !destinations) return null;
  if (destinations.none) {
    return (
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        No digest sent — this workspace has no Slack channel or email list for
        finished simulations.
      </Typography>
    );
  }
  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ minWidth: 0 }}
      >
        <Iconify
          icon="solar:letter-linear"
          width={14}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Typography
          sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
        >
          {`Digest sent to ${destinations.label}`}
          {!destinations.slackConnected && (
            <>
              {" · Slack isn't connected — "}
              {/* Where the org's notification settings live; no new preference surface. */}
              <Box
                component="span"
                role="button"
                tabIndex={0}
                onClick={() =>
                  navigateTo(paths.dashboard.settings.integrations)
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter")
                    navigateTo(paths.dashboard.settings.integrations);
                }}
                sx={{
                  color: "text.primary",
                  fontWeight: 600,
                  cursor: "pointer",
                  textDecoration: "underline",
                  textDecorationColor: (t) =>
                    alpha(t.palette.text.primary, 0.3),
                  textUnderlineOffset: "3px",
                  "&:hover": { textDecorationColor: "currentColor" },
                }}
              >
                Connect Slack
              </Box>
            </>
          )}
          {" · "}
          <Box
            component="span"
            role="button"
            tabIndex={0}
            onClick={(e) => setAnchor(e.currentTarget)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setAnchor(e.currentTarget);
            }}
            sx={{
              color: "text.primary",
              fontWeight: 600,
              cursor: "pointer",
              textDecoration: "underline",
              textDecorationColor: (t) => alpha(t.palette.text.primary, 0.3),
              textUnderlineOffset: "3px",
              "&:hover": { textDecorationColor: "currentColor" },
            }}
          >
            Preview
          </Box>
        </Typography>
      </Stack>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { width: 460, maxWidth: "94vw", borderRadius: "8px", mb: 0.75 },
          },
        }}
      >
        <DigestPreview
          digest={digest}
          destinations={destinations}
          sentAt={sentAt}
          onNavigate={() => setAnchor(null)}
        />
      </Popover>
    </>
  );
}

DigestLine.propTypes = {
  digest: PropTypes.object,
  destinations: PropTypes.object,
  sentAt: PropTypes.string,
};

/* The digest as each destination received it. */
export function DigestPreview({ digest, destinations, sentAt, onNavigate }) {
  const navigate = useNavigate();
  const go = (href) => {
    onNavigate?.();
    navigate(href);
  };
  const open = (label) =>
    go(
      digest.links.find((l) => l.label === label)?.href || digest.links[0].href,
    );
  return (
    <Stack spacing={1.5} sx={{ p: 2 }}>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Run digest
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Sent once when the run finished, to where this workspace already sends
          notifications. Change routing in Settings → Integrations.
        </Typography>
      </Box>

      {destinations.channel && (
        <Box>
          <Typography
            sx={{ ...colHead, mb: 0.5 }}
          >{`Slack · ${destinations.channel}`}</Typography>
          <SlackMessage
            msg={{
              channel: destinations.channel,
              title: digest.title,
              lines: digest.lines,
              actions: digest.links.map((l) => l.label),
              at: sentAt,
            }}
            onAction={open}
          />
        </Box>
      )}

      {destinations.emails.length > 0 && (
        <Box>
          <Typography sx={{ ...colHead, mb: 0.5 }}>
            {`Email · ${destinations.emails.map((p) => p.name).join(", ")}`}
          </Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              px: 1.5,
              py: 1.25,
            }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {digest.title}
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
            >
              {digest.score}
            </Typography>
            {digest.summary && (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {digest.summary}
              </Typography>
            )}
            {digest.caveat && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {digest.caveat}
              </Typography>
            )}
            {digest.issues.length > 0 && (
              <Stack spacing={0.25} sx={{ mt: 0.75 }}>
                {digest.issues.map((i, k) => (
                  <Typography
                    key={i.id}
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`${k + 1}. `}
                    <Link
                      href={i.href}
                      onClick={(e) => {
                        e.preventDefault();
                        go(i.href);
                      }}
                      underline="always"
                      sx={{
                        color: "text.primary",
                        textDecorationColor: (t) =>
                          alpha(t.palette.text.primary, 0.3),
                      }}
                    >
                      {i.text}
                    </Link>
                  </Typography>
                ))}
                {digest.more > 0 && (
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >{`+${digest.more} more in ${SHOW_INSIGHTS ? "Insights" : "the run"}`}</Typography>
                )}
              </Stack>
            )}
            <Stack direction="row" spacing={1.5} sx={{ mt: 1 }}>
              {digest.links.map((l) => (
                <Link
                  key={l.label}
                  href={l.href}
                  onClick={(e) => {
                    e.preventDefault();
                    go(l.href);
                  }}
                  underline="always"
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.primary",
                  }}
                >
                  {l.label}
                </Link>
              ))}
            </Stack>
          </Box>
        </Box>
      )}
    </Stack>
  );
}

DigestPreview.propTypes = {
  digest: PropTypes.object,
  destinations: PropTypes.object,
  sentAt: PropTypes.string,
  onNavigate: PropTypes.func,
};
