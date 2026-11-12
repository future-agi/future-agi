import { Box, IconButton, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import {
  getDefaultNodeData,
  getNodeLabel,
  NODE_TYPES,
} from "../store/graphStore";
import { ShowComponent } from "src/components/show";
import ConfigureConversationNode from "./ConfigureConversationNode";
import ConfigureEndCallNode from "./ConfigureEndCallNode";
import ConfigureTransferCallNode from "./ConfigureTransferCallNode";

const ConfigureNodeForm = ({ onClose, node, onChange }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "100%",
      }}
    >
      <Box
        sx={{
          padding: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          {node?.data?.name}
        </Typography>
        <IconButton
          onClick={onClose}
          sx={{
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <Box
        sx={{ padding: 2, display: "flex", flexDirection: "column", gap: 3 }}
      >
        <FormSearchSelectFieldState
          label="Node Type"
          onChange={(e) => {
            const newNodeType = e.target.value;
            onChange(node?.id, (existingNode) => ({
              ...existingNode,
              type: newNodeType,
              data: getDefaultNodeData(newNodeType),
            }));
          }}
          options={Object.values(NODE_TYPES).map((value) => ({
            label: getNodeLabel(value),
            value: value,
          }))}
          value={node?.type}
          fullWidth
          size="small"
        />
        <ShowComponent condition={node?.type === NODE_TYPES.CONVERSATION}>
          <ConfigureConversationNode node={node} onChange={onChange} />
        </ShowComponent>
        <ShowComponent
          condition={
            node?.type === NODE_TYPES.END || node?.type === NODE_TYPES.END_CHAT
          }
        >
          <ConfigureEndCallNode node={node} onChange={onChange} />
        </ShowComponent>
        <ShowComponent
          condition={
            node?.type === NODE_TYPES.TRANSFER ||
            node?.type === NODE_TYPES.TRANSFER_CHAT
          }
        >
          <ConfigureTransferCallNode node={node} onChange={onChange} />
        </ShowComponent>
      </Box>
    </Box>
  );
};

ConfigureNodeForm.propTypes = {
  onClose: PropTypes.func,
  node: PropTypes.object,
  onChange: PropTypes.func,
};

export default ConfigureNodeForm;
