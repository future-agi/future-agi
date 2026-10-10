import * as React from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import PropTypes from "prop-types";
import { InputAdornment, TextField } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { useState, useMemo } from "react";
import { TreeView } from "../../../../TreeView";
import { CustomTreeNode } from "./CustomTreeNode";

export default function NodeOutputListView({
  showTitle = true,
  showSearch = true,
  currentAgent,
  nodes,
  selectedNodeId,
  onNodeSelect,
}) {
  const [searchQuery, setSearchQuery] = useState("");
  const filteredNodes = useMemo(() => {
    if (!nodes?.length) return [];
    if (!searchQuery?.trim()) return nodes;
    const query = searchQuery.toLowerCase();

    const filterNode = (node) => {
      const nameMatches = (node?.name ?? "").toLowerCase().includes(query);
      if (node?.children?.length) {
        const matchingChildren = node.children.map(filterNode).filter(Boolean);
        if (matchingChildren.length > 0) {
          return { ...node, children: matchingChildren };
        }
      }
      return nameMatches ? node : null;
    };

    return nodes.map(filterNode).filter(Boolean);
  }, [nodes, searchQuery]);

  return (
    <Box
      sx={{
        minWidth: 360,
        maxWidth: 420,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        bgcolor: "background.paper",
      }}
    >
      {/* Title and Search */}
      <Box sx={{ p: 2, pb: 1.5 }}>
        {showTitle && (
          <Typography
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
            sx={{ mb: 1.5 }}
          >
            {currentAgent?.name || "Agent"} Logs
          </Typography>
        )}

        {showSearch && (
          <TextField
            placeholder="Search"
            size="small"
            variant="outlined"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            fullWidth
            sx={{
              "& .MuiInputBase-root": {
                height: 36,
                fontSize: "13px",
              },
              "& .MuiOutlinedInput-root": {
                borderRadius: 1,
              },
            }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SvgColor
                    src="/assets/icons/app/ic_search.svg"
                    sx={{ width: 16, height: 16, bgcolor: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
          />
        )}
      </Box>

      {/* Node Tree */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          px: 2,
          py: 1,
        }}
      >
        {!filteredNodes || filteredNodes.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              py: 4,
              mb: 5,
            }}
          >
            <Typography typography="s2" color="text.disabled">
              {searchQuery ? "No matching nodes" : "No nodes to display"}
            </Typography>
          </Box>
        ) : (
          <TreeView
            data={filteredNodes || []}
            customNodeComponent={CustomTreeNode}
            selectedNodeId={selectedNodeId}
            onNodeSelect={onNodeSelect}
          />
        )}
      </Box>
    </Box>
  );
}

NodeOutputListView.propTypes = {
  showTitle: PropTypes.bool,
  showSearch: PropTypes.bool,
  currentAgent: PropTypes.object,
  nodes: PropTypes.array.isRequired,
  selectedNodeId: PropTypes.string,
  onNodeSelect: PropTypes.func.isRequired,
};
