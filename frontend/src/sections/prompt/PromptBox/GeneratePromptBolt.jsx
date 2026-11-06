import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import { Box, Button, Typography } from "@mui/material";
import { Events, trackEvent } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const BlockEmbed = Quill.import("blots/block/embed");

const GeneratePrompt = ({ onGenerate }) => {
  return (
    <Box
      sx={{ paddingBottom: 1, gap: 1, display: "flex", alignItems: "center" }}
    >
      <Button
        startIcon={<Iconify icon="solar:pen-linear" />}
        onClick={() => {
          onGenerate();
          trackEvent(Events.generatePromptClicked);
        }}
        sx={{
          paddingLeft: 2,
          paddingRight: 2,
          fontFamily: "IBM Plex Sans, sans-serif",
          color: "primary.main",
          backgroundColor: "action.selected",
          boxShadow: "none",
          lineHeight: 1,
          "&:hover": {
            backgroundColor: "action.hover",
            boxShadow: "none",
          },
          "&:active": {
            boxShadow: "none",
          },
          "&.Mui-disabled": {
            backgroundColor: "action.disabledBackground",
          },
        }}
      >
        Generate Prompt
      </Button>
      <Typography
        color="text.secondary"
        fontFamily="IBM Plex Sans, sans-serif"
        fontSize="13px"
      >
        or enter instructions or prompt..
      </Typography>
    </Box>
  );
};

GeneratePrompt.propTypes = {
  onGenerate: PropTypes.func,
};

class GeneratePromptBolt extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    //node should take width of content not parent
    node.style.width = "fit-content";
    node.setAttribute("data-type", `generatePrompt-${value.id}`);
    // Render React component
    const root = createRoot(node);

    root.render(<GeneratePrompt onGenerate={value.onGenerate} />);

    return node;
  }

  static formats() {
    return null;
  }

  remove() {
    return;
  }

  // Add value method to properly handle the blot's value
  static value(node) {
    return {
      id: node.getAttribute("id"),
    };
  }
}

GeneratePromptBolt.blotName = "generatePrompt";
GeneratePromptBolt.tagName = "div";

export default GeneratePromptBolt;
