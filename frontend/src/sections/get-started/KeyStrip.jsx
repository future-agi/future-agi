import React, { useState } from "react";
import PropTypes from "prop-types";
import { IconButton, Stack, Tooltip, Typography, Button } from "@mui/material";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import Iconify from "src/components/iconify";

/* The workspace's keys on one line — masked, each copied in full — with the
   self-hosting hint and Manage keys. `keys` is useFiKeys(). */
function KeyValue({ name, value, masked }) {
  const [copied, setCopied] = useState(false);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ minWidth: 0 }}
    >
      <Typography
        sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
      >
        {name}
      </Typography>
      <Typography
        noWrap
        sx={{ fontFamily: "monospace", fontSize: 12, color: "text.primary" }}
      >
        {masked}
      </Typography>
      <Tooltip title={copied ? "Copied" : `Copy ${name}`}>
        <IconButton
          size="small"
          aria-label={`Copy ${name}`}
          onClick={() => {
            navigator.clipboard?.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={14}
          />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
KeyValue.propTypes = {
  name: PropTypes.string,
  value: PropTypes.string,
  masked: PropTypes.string,
};

/* One line under the header: the keys every snippet below already uses. */
export default function KeyStrip({ keys }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        minHeight: 40,
        flexWrap: "wrap",
        rowGap: 0.5,
        mb: 3,
      }}
    >
      <Iconify
        icon="solar:key-linear"
        width={16}
        sx={{ color: "text.tertiary" }}
      />
      {keys.isLoading ? (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Loading your keys…
        </Typography>
      ) : keys.has ? (
        <>
          <KeyValue
            name="FI_API_KEY"
            value={keys.api}
            masked={keys.mask(keys.api)}
          />
          <KeyValue
            name="FI_SECRET_KEY"
            value={keys.secret}
            masked={keys.mask(keys.secret)}
          />
          <Typography
            noWrap
            title="These keys are filled into every snippet below"
            sx={{
              typography: "s3",
              color: "text.tertiary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {keys.name || "Used below"}
          </Typography>
        </>
      ) : (
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          No keys yet — create one and it fills in every snippet below.
        </Typography>
      )}
      <Tooltip
        arrow
        title="Self-hosting? Point the SDK and CLI at your deployment with FI_BASE_URL."
      >
        <Iconify
          icon="solar:info-circle-linear"
          width={14}
          aria-label="Self-hosting"
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Tooltip>
      <Button
        component={RouterLink}
        href={paths.dashboard.keys}
        size="small"
        variant="text"
        sx={{ color: "text.secondary" }}
      >
        {keys.has ? "Manage keys" : "Create key"}
      </Button>
    </Stack>
  );
}
KeyStrip.propTypes = { keys: PropTypes.object };
