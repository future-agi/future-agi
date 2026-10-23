/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  TextField,
  MenuItem,
  FormControlLabel,
  Switch,
  Collapse,
  Divider,
  Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SecretField from "../components/SecretField";

// ---------- Collapsible section ----------
const Section = ({ title, subtitle, icon, children, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card variant="outlined">
      <CardContent
        sx={{
          py: 1.5,
          "&:last-child": { pb: 1.5 },
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify icon={icon} width={20} sx={{ color: "primary.main" }} />
            <Stack>
              <Typography variant="subtitle2">{title}</Typography>
              {subtitle && (
                <Typography variant="caption" color="text.secondary">
                  {subtitle}
                </Typography>
              )}
            </Stack>
          </Stack>
          <Iconify
            icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={20}
            sx={{ color: "text.secondary" }}
          />
        </Stack>
      </CardContent>
      <Collapse in={open}>
        <Divider />
        <CardContent sx={{ pt: 2 }} onClick={(e) => e.stopPropagation()}>
          {children}
        </CardContent>
      </Collapse>
    </Card>
  );
};

// ---------- L1 Backend configs ----------
const L1_BACKENDS = [
  { value: "memory", label: "Memory (In-Process)", icon: "mdi:memory" },
  { value: "disk", label: "Disk (Local File)", icon: "mdi:harddisk" },
  { value: "redis", label: "Redis", icon: "mdi:database" },
  { value: "s3", label: "Amazon S3", icon: "mdi:aws" },
  {
    value: "azure-blob",
    label: "Azure Blob Storage",
    icon: "mdi:microsoft-azure",
  },
  { value: "gcs", label: "Google Cloud Storage", icon: "mdi:google-cloud" },
];

const L2_BACKENDS = [
  { value: "memory", label: "Memory (In-Process)", icon: "mdi:memory" },
  { value: "qdrant", label: "Qdrant", icon: "mdi:vector-square" },
  { value: "weaviate", label: "Weaviate", icon: "mdi:database-search" },
  { value: "pinecone", label: "Pinecone", icon: "mdi:pine-tree" },
];

// ---------- Backend-specific config fields ----------
const DiskConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <TextField
      size="small"
      label="Directory"
      value={config.directory || ""}
      onChange={(e) => onChange({ ...config, directory: e.target.value })}
      fullWidth
      helperText="Path on the gateway server for cache files"
    />
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        type="number"
        label="Max Size (MB)"
        value={
          config.max_size_bytes
            ? Math.round(config.max_size_bytes / 1048576)
            : ""
        }
        onChange={(e) =>
          onChange({
            ...config,
            max_size_bytes: Number(e.target.value) * 1048576,
          })
        }
        sx={{ width: 140 }}
      />
      <TextField
        size="small"
        label="Cleanup Interval"
        value={config.cleanup_interval || "5m"}
        onChange={(e) =>
          onChange({ ...config, cleanup_interval: e.target.value })
        }
        sx={{ width: 150 }}
        helperText="e.g. 5m, 1h"
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={config.compress || false}
            onChange={(e) =>
              onChange({ ...config, compress: e.target.checked })
            }
          />
        }
        label={<Typography variant="body2">Compress</Typography>}
      />
    </Stack>
  </Stack>
);

const RedisConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="Address"
        value={config.address || ""}
        onChange={(e) => onChange({ ...config, address: e.target.value })}
        sx={{ flex: 1 }}
        placeholder="localhost:6379"
      />
      <TextField
        select
        size="small"
        label="Mode"
        value={config.mode || "single"}
        onChange={(e) => onChange({ ...config, mode: e.target.value })}
        sx={{ width: 140 }}
      >
        <MenuItem value="single">Single</MenuItem>
        <MenuItem value="sentinel">Sentinel</MenuItem>
        <MenuItem value="cluster">Cluster</MenuItem>
      </TextField>
    </Stack>
    <Stack direction="row" spacing={2}>
      <SecretField
        size="small"
        label="Password"
        value={config.password}
        onChange={(v) => onChange({ ...config, password: v })}
        sx={{ width: 200 }}
      />
      <TextField
        size="small"
        type="number"
        label="DB"
        value={config.db ?? 0}
        onChange={(e) => onChange({ ...config, db: Number(e.target.value) })}
        sx={{ width: 80 }}
      />
      <TextField
        size="small"
        type="number"
        label="Pool Size"
        value={config.pool_size ?? ""}
        onChange={(e) =>
          onChange({ ...config, pool_size: Number(e.target.value) })
        }
        sx={{ width: 100 }}
      />
      <TextField
        size="small"
        label="Key Prefix"
        value={config.key_prefix || ""}
        onChange={(e) => onChange({ ...config, key_prefix: e.target.value })}
        sx={{ width: 140 }}
      />
    </Stack>
    <Stack direction="row" spacing={2} alignItems="center">
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={config.tls || false}
            onChange={(e) => onChange({ ...config, tls: e.target.checked })}
          />
        }
        label={<Typography variant="body2">TLS</Typography>}
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={config.compress || false}
            onChange={(e) =>
              onChange({ ...config, compress: e.target.checked })
            }
          />
        }
        label={<Typography variant="body2">Compress</Typography>}
      />
    </Stack>
    {(config.mode === "sentinel" || config.mode === "cluster") && (
      <TextField
        size="small"
        label="Addresses (comma-separated)"
        value={(config.addresses || []).join(", ")}
        onChange={(e) =>
          onChange({
            ...config,
            addresses: e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          })
        }
        fullWidth
        helperText="Additional addresses for sentinel/cluster mode"
      />
    )}
  </Stack>
);

const S3Config = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="Bucket"
        value={config.bucket || ""}
        onChange={(e) => onChange({ ...config, bucket: e.target.value })}
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Prefix"
        value={config.prefix || ""}
        onChange={(e) => onChange({ ...config, prefix: e.target.value })}
        sx={{ width: 160 }}
        placeholder="agentcc-cache/"
      />
      <TextField
        select
        size="small"
        label="Region"
        value={config.region || "us-east-1"}
        onChange={(e) => onChange({ ...config, region: e.target.value })}
        sx={{ width: 150 }}
      >
        {[
          "us-east-1",
          "us-west-2",
          "eu-west-1",
          "eu-central-1",
          "ap-southeast-1",
          "ap-northeast-1",
        ].map((r) => (
          <MenuItem key={r} value={r}>
            {r}
          </MenuItem>
        ))}
      </TextField>
    </Stack>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="Access Key ID"
        type="password"
        value={config.access_key_id || ""}
        onChange={(e) => onChange({ ...config, access_key_id: e.target.value })}
        sx={{ flex: 1 }}
      />
      <SecretField
        size="small"
        label="Secret Access Key"
        value={config.secret_access_key}
        onChange={(v) => onChange({ ...config, secret_access_key: v })}
        sx={{ flex: 1 }}
      />
    </Stack>
    <FormControlLabel
      control={
        <Switch
          size="small"
          checked={config.compress || false}
          onChange={(e) => onChange({ ...config, compress: e.target.checked })}
        />
      }
      label={<Typography variant="body2">Compress</Typography>}
    />
  </Stack>
);

const AzureBlobConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="Container"
        value={config.container || ""}
        onChange={(e) => onChange({ ...config, container: e.target.value })}
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Prefix"
        value={config.prefix || ""}
        onChange={(e) => onChange({ ...config, prefix: e.target.value })}
        sx={{ width: 160 }}
      />
    </Stack>
    <SecretField
      size="small"
      label="Connection String"
      value={config.connection_string}
      onChange={(v) => onChange({ ...config, connection_string: v })}
      fullWidth
    />
    <SecretField
      size="small"
      label="SAS Token (alternative)"
      value={config.sas_token}
      onChange={(v) => onChange({ ...config, sas_token: v })}
      fullWidth
      helperText="Use either Connection String or SAS Token"
    />
    <FormControlLabel
      control={
        <Switch
          size="small"
          checked={config.compress || false}
          onChange={(e) => onChange({ ...config, compress: e.target.checked })}
        />
      }
      label={<Typography variant="body2">Compress</Typography>}
    />
  </Stack>
);

const GCSConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="Bucket"
        value={config.bucket || ""}
        onChange={(e) => onChange({ ...config, bucket: e.target.value })}
        sx={{ flex: 1 }}
      />
      <TextField
        size="small"
        label="Prefix"
        value={config.prefix || ""}
        onChange={(e) => onChange({ ...config, prefix: e.target.value })}
        sx={{ width: 160 }}
      />
      <TextField
        size="small"
        label="Project"
        value={config.project || ""}
        onChange={(e) => onChange({ ...config, project: e.target.value })}
        sx={{ width: 200 }}
      />
    </Stack>
    <TextField
      size="small"
      label="Credentials File Path"
      value={config.credentials_file || ""}
      onChange={(e) =>
        onChange({ ...config, credentials_file: e.target.value })
      }
      fullWidth
      helperText="Path to service account JSON on the gateway server"
    />
    <FormControlLabel
      control={
        <Switch
          size="small"
          checked={config.compress || false}
          onChange={(e) => onChange({ ...config, compress: e.target.checked })}
        />
      }
      label={<Typography variant="body2">Compress</Typography>}
    />
  </Stack>
);

// ---------- L2 backend-specific configs ----------
const QdrantConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="URL"
        value={config.url || ""}
        onChange={(e) => onChange({ ...config, url: e.target.value })}
        sx={{ flex: 1 }}
        placeholder="http://localhost:6333"
      />
      <TextField
        size="small"
        label="Collection"
        value={config.collection || ""}
        onChange={(e) => onChange({ ...config, collection: e.target.value })}
        sx={{ width: 200 }}
        placeholder="agentcc_semantic_cache"
      />
    </Stack>
    <SecretField
      size="small"
      label="API Key"
      value={config.api_key}
      onChange={(v) => onChange({ ...config, api_key: v })}
      sx={{ width: 300 }}
    />
  </Stack>
);

const WeaviateConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <Stack direction="row" spacing={2}>
      <TextField
        size="small"
        label="URL"
        value={config.url || ""}
        onChange={(e) => onChange({ ...config, url: e.target.value })}
        sx={{ flex: 1 }}
        placeholder="http://localhost:8080"
      />
      <TextField
        size="small"
        label="Class Name"
        value={config.class || ""}
        onChange={(e) => onChange({ ...config, class: e.target.value })}
        sx={{ width: 200 }}
        placeholder="AgentccSemanticCache"
      />
    </Stack>
    <SecretField
      size="small"
      label="API Key"
      value={config.api_key}
      onChange={(v) => onChange({ ...config, api_key: v })}
      sx={{ width: 300 }}
    />
  </Stack>
);

const PineconeConfig = ({ config, onChange }) => (
  <Stack spacing={1.5}>
    <TextField
      size="small"
      label="Index URL"
      value={config.url || ""}
      onChange={(e) => onChange({ ...config, url: e.target.value })}
      fullWidth
      placeholder="https://your-index-xxxxxx.svc.pinecone.io"
    />
    <SecretField
      size="small"
      label="API Key"
      value={config.api_key}
      onChange={(v) => onChange({ ...config, api_key: v })}
      sx={{ width: 300 }}
    />
  </Stack>
);

// ---------- Backend config renderers ----------
const L1_BACKEND_CONFIGS = {
  disk: DiskConfig,
  redis: RedisConfig,
  s3: S3Config,
  "azure-blob": AzureBlobConfig,
  gcs: GCSConfig,
};

const L2_BACKEND_CONFIGS = {
  qdrant: QdrantConfig,
  weaviate: WeaviateConfig,
  pinecone: PineconeConfig,
};

// ---------- Main component ----------
const CacheConfigTab = ({ cache, onChange }) => {
  const config = cache || {};
  const semantic = config.semantic || {};
  const edge = config.edge || {};

  const handleChange = (field, value) => {
    onChange({ ...config, [field]: value });
  };

  const handleSemanticChange = (field, value) => {
    onChange({ ...config, semantic: { ...semantic, [field]: value } });
  };

  const handleEdgeChange = (field, value) => {
    onChange({ ...config, edge: { ...edge, [field]: value } });
  };

  const handleBackendConfigChange = (backendKey, backendConfig) => {
    onChange({ ...config, [backendKey]: backendConfig });
  };

  const handleSemanticBackendConfigChange = (backendKey, backendConfig) => {
    onChange({
      ...config,
      semantic: { ...semantic, [backendKey]: backendConfig },
    });
  };

  const l1Backend = config.backend || "memory";
  const l2Backend = semantic.backend || "memory";

  const L1BackendConfigComponent = L1_BACKEND_CONFIGS[l1Backend];
  const L2BackendConfigComponent = L2_BACKEND_CONFIGS[l2Backend];

  // Map backend value to its config key in the config object
  const l1ConfigKey = {
    disk: "disk",
    redis: "redis",
    s3: "s3",
    "azure-blob": "azure_blob",
    gcs: "gcs",
  }[l1Backend];

  return (
    <Stack spacing={2}>
      {/* ===================== CACHE ENABLED + BASICS ===================== */}
      <Section
        title="Cache Settings"
        subtitle="Enable response caching and configure basic settings"
        icon="mdi:cached"
        defaultOpen
      >
        <Stack spacing={2}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={config.enabled || false}
                onChange={(e) => handleChange("enabled", e.target.checked)}
              />
            }
            label={
              <Typography variant="body2">Enable Response Cache</Typography>
            }
          />

          {config.enabled && (
            <Stack direction="row" spacing={2} flexWrap="wrap">
              <TextField
                size="small"
                label="Default TTL"
                value={config.default_ttl || config.defaultTtl || "5m"}
                onChange={(e) => handleChange("default_ttl", e.target.value)}
                sx={{ width: 120 }}
                helperText="e.g. 5m, 1h"
              />
              <TextField
                size="small"
                type="number"
                label="Max Entries"
                value={config.max_entries ?? config.maxEntries ?? 10000}
                onChange={(e) =>
                  handleChange("max_entries", Number(e.target.value))
                }
                sx={{ width: 130 }}
              />
            </Stack>
          )}
        </Stack>
      </Section>

      {config.enabled && (
        <>
          {/* ===================== L1 BACKEND ===================== */}
          <Section
            title="L1 Backend (Exact Match)"
            subtitle="Storage backend for exact request/response cache"
            icon="mdi:database"
          >
            <Stack spacing={2}>
              <Typography variant="body2" fontWeight={500}>
                Backend Type
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {L1_BACKENDS.map((b) => (
                  <Chip
                    key={b.value}
                    icon={<Iconify icon={b.icon} width={16} />}
                    label={b.label}
                    variant={l1Backend === b.value ? "filled" : "outlined"}
                    color={l1Backend === b.value ? "primary" : "default"}
                    onClick={() => handleChange("backend", b.value)}
                    sx={{ cursor: "pointer" }}
                  />
                ))}
              </Stack>

              {L1BackendConfigComponent && (
                <>
                  <Divider />
                  <Typography variant="body2" fontWeight={500}>
                    {L1_BACKENDS.find((b) => b.value === l1Backend)?.label}{" "}
                    Settings
                  </Typography>
                  <L1BackendConfigComponent
                    config={config[l1ConfigKey] || {}}
                    onChange={(val) =>
                      handleBackendConfigChange(l1ConfigKey, val)
                    }
                  />
                </>
              )}
            </Stack>
          </Section>

          {/* ===================== L2 SEMANTIC CACHE ===================== */}
          <Section
            title="L2 Semantic Cache"
            subtitle="Similarity-based cache for semantically equivalent queries"
            icon="mdi:brain"
          >
            <Stack spacing={2}>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={semantic.enabled || false}
                    onChange={(e) =>
                      handleSemanticChange("enabled", e.target.checked)
                    }
                  />
                }
                label={
                  <Typography variant="body2">Enable Semantic Cache</Typography>
                }
              />

              {semantic.enabled && (
                <>
                  <Stack direction="row" spacing={2} flexWrap="wrap">
                    <TextField
                      size="small"
                      type="number"
                      label="Similarity Threshold"
                      value={semantic.threshold ?? 0.85}
                      onChange={(e) =>
                        handleSemanticChange(
                          "threshold",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 160 }}
                      inputProps={{ step: 0.05, min: 0.5, max: 1 }}
                      helperText="0.5 - 1.0"
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Dimensions"
                      value={semantic.dimensions ?? 256}
                      onChange={(e) =>
                        handleSemanticChange(
                          "dimensions",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 120 }}
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Max Entries"
                      value={
                        semantic.max_entries ?? semantic.maxEntries ?? 5000
                      }
                      onChange={(e) =>
                        handleSemanticChange(
                          "max_entries",
                          Number(e.target.value),
                        )
                      }
                      sx={{ width: 130 }}
                    />
                  </Stack>

                  <Typography variant="body2" fontWeight={500}>
                    Vector Backend
                  </Typography>
                  <Stack direction="row" spacing={1} flexWrap="wrap">
                    {L2_BACKENDS.map((b) => (
                      <Chip
                        key={b.value}
                        icon={<Iconify icon={b.icon} width={16} />}
                        label={b.label}
                        variant={l2Backend === b.value ? "filled" : "outlined"}
                        color={l2Backend === b.value ? "primary" : "default"}
                        onClick={() => handleSemanticChange("backend", b.value)}
                        sx={{ cursor: "pointer" }}
                      />
                    ))}
                  </Stack>

                  {L2BackendConfigComponent && (
                    <>
                      <Divider />
                      <Typography variant="body2" fontWeight={500}>
                        {L2_BACKENDS.find((b) => b.value === l2Backend)?.label}{" "}
                        Settings
                      </Typography>
                      <L2BackendConfigComponent
                        config={semantic[l2Backend] || {}}
                        onChange={(val) =>
                          handleSemanticBackendConfigChange(l2Backend, val)
                        }
                      />
                    </>
                  )}
                </>
              )}
            </Stack>
          </Section>

          {/* ===================== EDGE CACHING ===================== */}
          <Section
            title="Edge Caching (CDN)"
            subtitle="HTTP cache headers for CDN and browser caching"
            icon="mdi:earth"
          >
            <Stack spacing={2}>
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={edge.enabled || false}
                    onChange={(e) =>
                      handleEdgeChange("enabled", e.target.checked)
                    }
                  />
                }
                label={
                  <Typography variant="body2">Enable Edge Caching</Typography>
                }
              />

              {edge.enabled && (
                <>
                  <Stack direction="row" spacing={2} flexWrap="wrap">
                    <TextField
                      size="small"
                      type="number"
                      label="Default TTL (seconds)"
                      value={edge.default_ttl ?? edge.defaultTtl ?? 300}
                      onChange={(e) =>
                        handleEdgeChange("default_ttl", Number(e.target.value))
                      }
                      sx={{ width: 170 }}
                    />
                    <TextField
                      size="small"
                      type="number"
                      label="Max Response Size (KB)"
                      value={
                        edge.max_size ? Math.round(edge.max_size / 1024) : ""
                      }
                      onChange={(e) =>
                        handleEdgeChange(
                          "max_size",
                          Number(e.target.value) * 1024,
                        )
                      }
                      sx={{ width: 180 }}
                      helperText="Max cacheable response"
                    />
                  </Stack>
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={
                          edge.require_opt_in ?? edge.requireOptIn ?? false
                        }
                        onChange={(e) =>
                          handleEdgeChange("require_opt_in", e.target.checked)
                        }
                      />
                    }
                    label={
                      <Typography variant="body2">
                        Require Opt-In (X-Cache-Control header)
                      </Typography>
                    }
                  />
                  <TextField
                    size="small"
                    label="Cacheable Models"
                    value={(
                      edge.cacheable_models ||
                      edge.cacheableModels ||
                      []
                    ).join(", ")}
                    onChange={(e) =>
                      handleEdgeChange(
                        "cacheable_models",
                        e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      )
                    }
                    fullWidth
                    helperText="Comma-separated model names. Leave empty for all models."
                  />
                </>
              )}
            </Stack>
          </Section>
        </>
      )}
    </Stack>
  );
};

export default CacheConfigTab;
