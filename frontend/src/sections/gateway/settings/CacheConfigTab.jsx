import React from "react";
import PropTypes from "prop-types";
import FormControlLabel from "@mui/material/FormControlLabel";
import Section from "src/components/section";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

// Cache in the org config editor: the org cache settings the gateway applies,
// whether this org's responses are cached and for how long
// (applyOrgCacheOverrides reads `enabled` and `default_ttl`, nothing else).
// Backend, max entries, disk path, semantic and edge settings are not
// offered: the gateway's own cache serves every org, so an org's values for
// them were never read. A value an older build stored for them stays in the
// config: every change here keeps the rest of the section as it is.
//
// `disabled` is caller-controlled. Node caching being off does not disable
// editing: saved settings apply when the gateway turns response caching on.
const CacheConfigTab = ({ cache, onChange, disabled = false }) => {
  const config = cache || {};
  const set = (field, value) => onChange({ ...config, [field]: value });

  return (
    <Section variant="panel">
      <Stack spacing={2}>
        <Stack spacing={0.25}>
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Response Cache
          </Typography>
          <Typography typography="s2" color="text.secondary">
            Serve a repeated request from the gateway&apos;s cache instead of
            calling the provider again.
          </Typography>
        </Stack>
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={config.enabled || false}
              disabled={disabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
          }
          label={<Typography typography="s1">Enable Response Cache</Typography>}
        />
        {config.enabled && (
          <TextField
            size="small"
            label="Default TTL"
            value={config.default_ttl ?? config.defaultTtl ?? "5m"}
            onChange={(e) => set("default_ttl", e.target.value)}
            disabled={disabled}
            sx={{ width: 220 }}
            helperText="Seconds, or a duration such as 5m or 1h"
          />
        )}
      </Stack>
    </Section>
  );
};

CacheConfigTab.propTypes = {
  cache: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

export default CacheConfigTab;
