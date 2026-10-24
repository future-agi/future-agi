import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { formatDate } from "../utils/formatters";
import CodeSnippet from "./CodeSnippet";
import { copyText } from "./copyText";
import {
  CAPABILITIES,
  formatPercent,
  formatPerMillion,
  formatTokenCount,
  hasListPrice,
  modelDisplayName,
  vendorLabel,
} from "./catalog";
import { curlSnippet, pythonSnippet } from "./snippets";

const Fact = ({ label, children }) => (
  <Box sx={{ minWidth: 0 }}>
    <Typography variant="caption" color="text.secondary" component="div">
      {label}
    </Typography>
    <Typography variant="body2" fontWeight={500} component="div">
      {children}
    </Typography>
  </Box>
);

Fact.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

const SectionTitle = ({ children }) => (
  <Typography variant="subtitle2" gutterBottom>
    {children}
  </Typography>
);

SectionTitle.propTypes = { children: PropTypes.node };

const listText = (value) =>
  Array.isArray(value) && value.length ? value.join(", ") : "—";

const billed = (upstream, markupPercent) =>
  upstream == null ? null : upstream * (1 + (Number(markupPercent) || 0) / 100);

function PricingTable({ model, markupPercent }) {
  if (!hasListPrice(model)) {
    return (
      <Typography variant="body2" color="text.secondary">
        This model is priced per request by the provider, so it has no list
        price. Managed requests are billed at the provider&apos;s reported cost
        + {formatPercent(markupPercent)}.
      </Typography>
    );
  }
  const rows = [
    {
      label: "Input",
      upstream: model.input_per_mtok,
      charged: model.billed_input_per_mtok,
    },
    {
      label: "Output",
      upstream: model.output_per_mtok,
      charged: model.billed_output_per_mtok,
    },
  ];
  if (model.cache_read_per_mtok != null) {
    rows.push({
      label: "Cached input",
      upstream: model.cache_read_per_mtok,
      charged: billed(model.cache_read_per_mtok, markupPercent),
    });
  }
  return (
    <Table size="small" aria-label="Pricing per 1M tokens">
      <TableHead>
        <TableRow>
          <TableCell sx={{ pl: 0 }}>Per 1M tokens</TableCell>
          <TableCell align="right">Provider price</TableCell>
          <TableCell align="right" sx={{ pr: 0 }}>
            Managed price
          </TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.label}>
            <TableCell sx={{ pl: 0 }}>{row.label}</TableCell>
            <TableCell align="right" sx={{ color: "text.secondary" }}>
              {formatPerMillion(row.upstream)}
            </TableCell>
            <TableCell align="right" sx={{ pr: 0, fontWeight: 600 }}>
              {formatPerMillion(row.charged)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

PricingTable.propTypes = {
  model: PropTypes.object.isRequired,
  markupPercent: PropTypes.number,
};

const ModelDetailDrawer = ({
  model,
  open,
  onClose,
  markupPercent,
  baseUrl,
  managedStatus,
}) => {
  const snippets = useMemo(
    () =>
      model
        ? [
            {
              key: "curl",
              label: "cURL",
              code: curlSnippet(baseUrl, model.id),
            },
            {
              key: "python",
              label: "Python",
              code: pythonSnippet(baseUrl, model.id),
            },
          ]
        : [],
    [model, baseUrl],
  );

  const params = Array.isArray(model?.supported_parameters)
    ? model.supported_parameters
    : [];
  const capabilities = model ? CAPABILITIES.filter((c) => c.test(model)) : [];

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 520 } } }}
    >
      {model && (
        <>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="flex-start"
            spacing={2}
            sx={{ p: 2.5, borderBottom: 1, borderColor: "divider" }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h6" sx={{ wordBreak: "break-word" }}>
                  {modelDisplayName(model)}
                </Typography>
                <Chip
                  size="small"
                  variant="outlined"
                  label={vendorLabel(model.vendor)}
                />
              </Stack>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
                >
                  {model.id}
                </Typography>
                <Tooltip title="Copy model ID" arrow>
                  <IconButton
                    size="small"
                    aria-label="Copy model ID"
                    onClick={() => copyText(model.id, "Model ID copied")}
                  >
                    <Iconify icon="mdi:content-copy" width={14} />
                  </IconButton>
                </Tooltip>
              </Stack>
            </Box>
            <IconButton onClick={onClose} edge="end" aria-label="Close">
              <Iconify icon="mdi:close" width={20} />
            </IconButton>
          </Stack>

          <Stack spacing={3} sx={{ p: 2.5, overflowY: "auto", flex: 1 }}>
            {model.description && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ whiteSpace: "pre-line" }}
              >
                {model.description}
              </Typography>
            )}

            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                gap: 2,
              }}
            >
              <Fact label="Context window">
                {formatTokenCount(model.context_length)} tokens
              </Fact>
              <Fact label="Max output">
                {formatTokenCount(model.max_output_tokens)}
                {model.max_output_tokens ? " tokens" : ""}
              </Fact>
              <Fact label="Input">{listText(model.input_modalities)}</Fact>
              <Fact label="Output">{listText(model.output_modalities)}</Fact>
              <Fact label="Released">
                {model.created
                  ? formatDate(new Date(model.created * 1000).toISOString())
                  : "—"}
              </Fact>
            </Box>

            <Box>
              <SectionTitle>Pricing</SectionTitle>
              <PricingTable model={model} markupPercent={markupPercent} />
              <Typography
                variant="caption"
                color="text.secondary"
                component="p"
                sx={{ mt: 1 }}
              >
                Managed requests are billed at provider cost +{" "}
                {formatPercent(markupPercent)}. Requests through your own key
                for this provider are billed by that provider, with no markup.
              </Typography>
            </Box>

            <Box>
              <SectionTitle>Capabilities</SectionTitle>
              {capabilities.length ? (
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {capabilities.map((c) => (
                    <Tooltip key={c.key} title={c.description} arrow>
                      <Chip
                        size="small"
                        variant="outlined"
                        icon={<Iconify icon={c.icon} width={16} />}
                        label={c.label}
                      />
                    </Tooltip>
                  ))}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Text in, text out. No tools, structured output, image input or
                  reasoning controls listed.
                </Typography>
              )}
              {params.length > 0 && (
                <Box mt={1.5}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    component="div"
                    mb={0.5}
                  >
                    Supported request parameters
                  </Typography>
                  <Stack
                    direction="row"
                    spacing={0.5}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    {params.map((p) => (
                      <Chip
                        key={p}
                        size="small"
                        variant="outlined"
                        label={p}
                        sx={{ fontFamily: "monospace", fontSize: "0.7rem" }}
                      />
                    ))}
                  </Stack>
                </Box>
              )}
            </Box>

            <Divider />

            <Box>
              <SectionTitle>Call this model</SectionTitle>
              {managedStatus?.available && !managedStatus?.enabled && (
                <Alert severity="info" sx={{ mb: 1.5 }}>
                  Turn on managed models above to call this model without your
                  own provider key.
                </Alert>
              )}
              <CodeSnippet
                snippets={snippets}
                ariaLabel="Request example language"
              />
              <Typography
                variant="caption"
                color="text.secondary"
                component="p"
                sx={{ mt: 1 }}
              >
                Set AGENTCC_API_KEY to a gateway API key from API Keys.
              </Typography>
            </Box>
          </Stack>
        </>
      )}
    </Drawer>
  );
};

ModelDetailDrawer.propTypes = {
  model: PropTypes.object,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  markupPercent: PropTypes.number,
  baseUrl: PropTypes.string,
  managedStatus: PropTypes.object,
};

export default ModelDetailDrawer;
