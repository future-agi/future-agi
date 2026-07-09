import {
  Box,
  FormControlLabel,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";

const ConfigureConversationNode = ({ node, onChange }) => {
  const isGlobal = node?.data?.isGlobal ?? false;

  const handleGlobalToggle = (e) => {
    onChange(node?.id, (existingNode) => ({
      ...existingNode,
      data: {
        ...existingNode.data,
        isGlobal: e.target.checked,
      },
    }));
  };

  return (
    <>
      {/* <TextField
        label="First Message"
        value={node?.data?.firstMessage || ""}
        onChange={(e) =>
          onChange((existingNode) => ({
            ...existingNode,
            data: {
              ...existingNode.data,
              firstMessage: e.target.value,
            },
          }))
        }
        size="small"
        multiline
        rows={4}
        fullWidth
        placeholder="First message for the conversation"
      /> */}
      <TextField
        label="Prompt"
        value={node?.data?.prompt || ""}
        onChange={(e) => {
          onChange(node?.id, (existingNode) => ({
            ...existingNode,
            data: { ...existingNode.data, prompt: e.target.value },
          }));
        }}
        size="small"
        multiline
        rows={4}
        fullWidth
        placeholder="Enter prompt for conversation"
      />
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            padding: 2,
            borderColor: "divider",
          }}
        >
          <Box>
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Enable Global Node
            </Typography>
            <Typography typography="s1" color="text.secondary">
              Make this node available from any point in the conversation
            </Typography>
          </Box>
          <FormControlLabel
            control={
              <Switch
                checked={isGlobal}
                onChange={handleGlobalToggle}
                sx={{
                  "& .MuiSwitch-switchBase.Mui-checked": {
                    color: "primary.main",
                  },
                  "& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track": {
                    backgroundColor: "primary.main",
                  },
                }}
              />
            }
            label=""
            sx={{ marginRight: 0, marginTop: 0 }}
          />
        </Box>
      </Box>
    </>
  );
};

ConfigureConversationNode.propTypes = {
  node: PropTypes.object,
  onChange: PropTypes.func,
};

export default ConfigureConversationNode;
