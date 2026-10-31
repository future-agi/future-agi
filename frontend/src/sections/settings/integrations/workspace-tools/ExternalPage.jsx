import PropTypes from "prop-types";
import { Box, CircularProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "./clickable";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * The page inside the GitHub install window (/integrations-connect/github-install).
 * Once the GitHub App backend exists this is GitHub's own page; until then
 * it's a stand-in, with the address it would have on top.
 */
export default function StandInWindow({ site, url, children, footer }) {
  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        bgcolor: "background.default",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:lock-keyhole-minimalistic-linear"
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {url}
        </Typography>
      </Stack>
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          justifyContent: "center",
          px: 2,
          py: 4,
        }}
      >
        <Box sx={{ width: "100%", maxWidth: 560 }}>{children}</Box>
      </Box>
      {footer && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 3,
            py: 1.75,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          {footer}
        </Stack>
      )}
    </Box>
  );
}
StandInWindow.propTypes = {
  site: PropTypes.string,
  url: PropTypes.string,
  children: PropTypes.node,
  footer: PropTypes.node,
};

/** Steps within the provider's flow. */
export function HandoffSteps({ steps, current, sx }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={sx}>
      {steps.map((s, i) => (
        <Stack key={s} direction="row" alignItems="center" spacing={1}>
          {i > 0 && (
            <Box sx={{ width: 16, height: "1px", bgcolor: "divider" }} />
          )}
          <Box
            sx={{
              width: 18,
              height: 18,
              borderRadius: "50%",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
              border: "1px solid",
              borderColor: i <= current ? "text.primary" : "divider",
              bgcolor: i < current ? "text.primary" : "transparent",
              color: i < current ? "background.paper" : "text.primary",
            }}
          >
            {i < current ? (
              <Iconify icon="mdi:check" width={11} />
            ) : (
              <Typography
                sx={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: i === current ? "text.primary" : "text.disabled",
                }}
              >
                {i + 1}
              </Typography>
            )}
          </Box>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: i === current ? 700 : 500,
              color: i === current ? "text.primary" : "text.subtitle",
              whiteSpace: "nowrap",
            }}
          >
            {s}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
HandoffSteps.propTypes = {
  steps: PropTypes.arrayOf(PropTypes.string),
  current: PropTypes.number,
  sx: PropTypes.object,
};

/** The redirect back — the token exchange happens here, out of sight. */
export function Returning({ label }) {
  return (
    <Stack alignItems="center" spacing={1.25} sx={{ py: 5 }}>
      <CircularProgress
        size={20}
        thickness={5}
        sx={{ color: "text.secondary" }}
      />
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {label}
      </Typography>
    </Stack>
  );
}
Returning.propTypes = { label: PropTypes.string };

/**
 * Future AGI while the provider window is open: "finish over there". Also
 * covers a blocked popup, which browsers do when the click didn't open it.
 */
export function WaitingForWindow({ site, status, onFocus, onOpen, children }) {
  if (status === "blocked") {
    return (
      <Stack
        alignItems="center"
        spacing={1}
        sx={{ py: 4, textAlign: "center" }}
      >
        <Iconify
          icon="solar:danger-circle-linear"
          width={22}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          sx={{ typography: "s1", fontWeight: 700 }}
        >{`Your browser blocked the ${site} window`}</Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", maxWidth: 380 }}
        >{`Open it from here, or allow pop-ups for this site.`}</Typography>
        <Box sx={{ pt: 1 }}>{onOpen}</Box>
      </Stack>
    );
  }
  return (
    <Stack alignItems="center" spacing={1} sx={{ py: 4, textAlign: "center" }}>
      <Box
        sx={{
          position: "relative",
          width: 44,
          height: 44,
          display: "grid",
          placeItems: "center",
          mb: 0.5,
        }}
      >
        <CircularProgress
          size={44}
          thickness={2}
          sx={{ color: "text.disabled", position: "absolute", inset: 0 }}
        />
        <Iconify
          icon="solar:arrow-right-up-linear"
          width={18}
          sx={{ color: "text.secondary" }}
        />
      </Box>
      <Typography
        sx={{ typography: "s1", fontWeight: 700 }}
      >{`Finish in the ${site} window`}</Typography>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", maxWidth: 400 }}
      >
        {children}
      </Typography>
      <Typography
        {...clickable(onFocus)}
        sx={{
          typography: "s3",
          fontWeight: 700,
          color: "text.secondary",
          cursor: "pointer",
          pt: 0.5,
          "&:hover": { color: "text.primary", textDecoration: "underline" },
          ...focusRing,
        }}
      >
        {`Can't see it? Bring the ${site} window forward`}
      </Typography>
    </Stack>
  );
}
WaitingForWindow.propTypes = {
  site: PropTypes.string,
  status: PropTypes.string,
  onFocus: PropTypes.func,
  onOpen: PropTypes.node,
  children: PropTypes.node,
};
