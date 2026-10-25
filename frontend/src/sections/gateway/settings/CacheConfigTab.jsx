/* eslint-disable react/prop-types */
import React from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  TextField,
  FormControlLabel,
  Switch,
} from "@mui/material";
import Iconify from "src/components/iconify";

// The org cache settings the gateway applies: whether this org's responses are
// cached, and for how long (applyOrgCacheOverrides applies `enabled` and
// `default_ttl`; a stored `backend` is copied into the request's metadata and
// selects nothing). Backend, max entries, disk path, semantic and edge
// settings are not offered: the gateway's own cache serves every org.
// A stored value for them stays in the config: every change here keeps the
// rest of the section as it is.
//
// `disabled`: the gateway reports that it does not run response caching
// (org_sections_enforced); the editor says so above this tab.
const CacheConfigTab = ({ cache, onChange, disabled = false }) => {
  const config = cache || {};

  const handleChange = (field, value) => {
    onChange({ ...config, [field]: value });
  };

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon="mdi:cached"
              width={20}
              sx={{ color: "text.secondary" }}
            />
            <Stack>
              <Typography variant="subtitle2">Response Cache</Typography>
              <Typography variant="caption" color="text.secondary">
                Serve a repeated request from the gateway&apos;s cache instead
                of calling the provider again.
              </Typography>
            </Stack>
          </Stack>

          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={config.enabled || false}
                disabled={disabled}
                onChange={(e) => handleChange("enabled", e.target.checked)}
              />
            }
            label={
              <Typography variant="body2">Enable Response Cache</Typography>
            }
          />

          {config.enabled && (
            <TextField
              size="small"
              label="Default TTL"
              value={config.default_ttl ?? config.defaultTtl ?? "5m"}
              onChange={(e) => handleChange("default_ttl", e.target.value)}
              disabled={disabled}
              sx={{ width: 220 }}
              helperText="Seconds, or a duration such as 5m or 1h"
            />
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

export default CacheConfigTab;
