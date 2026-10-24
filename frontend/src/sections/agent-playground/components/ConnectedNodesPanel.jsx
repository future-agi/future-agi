import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import SvgColor from "src/components/svg-color";
import { NODE_TYPE_CONFIG } from "../utils/constants";

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    enqueueSnackbar("Copied to clipboard", { variant: "success" });
  });
}

function ConnectedNodeItem({ node }) {
  const config = NODE_TYPE_CONFIG[node.type];
  const variableName = `${node.label}.${node.outputLabel}`;

  return (
    <Box
      sx={{
        p: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        "&:hover .copy-btn": { opacity: 1 },
      }}
    >
      <Stack direction="row" alignItems="flex-start" gap={0.75}>
        {/* Node type icon */}
        <Box
          sx={{
            width: 24,
            height: 24,
            borderRadius: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: config?.color || "grey.500",
            flexShrink: 0,
            mt: 0.5,
          }}
        >
          <SvgColor
            src={config?.iconSrc || "/assets/icons/ic_chat_single.svg"}
            sx={{ width: 14, height: 14, color: "common.white" }}
          />
        </Box>

        <Stack direction={"column"} alignItems={"flex-start"}>
          {/* Node label */}
          <Typography
            variant="caption"
            fontWeight="fontWeightMedium"
            noWrap
            sx={{ flex: 1, minWidth: 0 }}
            title={node.label}
          >
            {node.label}
          </Typography>
          {/* Output label */}
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 0.25, display: "block", fontSize: "0.75rem" }}
          >
            {node.outputLabel}
          </Typography>
        </Stack>

        {/* Copy variable button */}
        <Tooltip title={`Copy {{${variableName}}}`} placement="top" arrow>
          <IconButton
            className="copy-btn"
            size="small"
            onClick={() => copyToClipboard(`{{${variableName}}}`)}
            sx={{ opacity: 0, transition: "opacity 0.15s", p: 0.25 }}
          >
            <SvgColor
              src="/assets/icons/ic_copy.svg"
              sx={{ width: 14, height: 14 }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
    </Box>
  );
}

ConnectedNodeItem.propTypes = {
  node: PropTypes.shape({
    id: PropTypes.string.isRequired,
    type: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    outputLabel: PropTypes.string.isRequired,
  }).isRequired,
};

export default function ConnectedNodesPanel({ connectedNodes }) {
  if (!connectedNodes?.length) return null;

  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Typography
        variant="caption"
        fontWeight="fontWeightBold"
        color="text.secondary"
        sx={{ mb: 1, textTransform: "none", letterSpacing: 0.5 }}
      >
        Inputs
      </Typography>

      <Stack spacing={0.75} sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {connectedNodes.map((node) => (
          <ConnectedNodeItem key={node.id} node={node} />
        ))}
      </Stack>
    </Box>
  );
}

ConnectedNodesPanel.propTypes = {
  connectedNodes: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      type: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      outputLabel: PropTypes.string.isRequired,
    }),
  ),
};
