// CustomImageBlot.js
import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import { Box, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import Image from "src/components/image";
import Iconify from "src/components/iconify";

const BlockEmbed = Quill.import("blots/block/embed");

// React Component for the Image Container
const ImageComponent = ({
  src,
  alt,
  id,
  setSelectedImageId,
  removeImageBlot,
}) => {
  // const { selectedImageId, setSelectedImageId } = useContext(EditorContext);

  return (
    <Box
      component="div"
      sx={{
        marginRight: 1,
        position: "relative",
        "&:hover .overlay": {
          opacity: 1,
          visibility: "visible",
        },
        cursor: "pointer",
        display: "inline-block",
        marginBottom: 1,
      }}
      data-id={id}
    >
      <Image src={src} alt={alt} sx={{ width: "140px", borderRadius: 1 }} />
      <Box
        className="overlay"
        position="absolute"
        top={0}
        left={0}
        right={0}
        bottom={0}
        height="100%"
        display="flex"
        alignItems="center"
        justifyContent="center"
        borderRadius={1}
        gap="5px"
        bgcolor="rgba(0,0,0,0.5)"
        sx={{
          opacity: 0,
          visibility: "hidden",
          transition: "opacity 0.2s ease, visibility 0.2s ease",
        }}
      >
        <IconButton
          sx={{ color: "common.white" }}
          onClick={() => {
            setSelectedImageId({ id, src });
          }}
        >
          <Iconify icon="solar:magnifier-zoom-in-linear" />
        </IconButton>
        <IconButton
          sx={{ color: "common.white" }}
          onClick={() => {
            setSelectedImageId({ id, src });
          }}
        >
          <Iconify icon="solar:refresh-linear" />
        </IconButton>
        <IconButton
          sx={{ color: "common.white" }}
          onClick={() => removeImageBlot(id)}
        >
          <Iconify icon="solar:trash-bin-trash-linear" />
        </IconButton>
      </Box>
    </Box>
  );
};

ImageComponent.propTypes = {
  src: PropTypes.string,
  alt: PropTypes.string,
  id: PropTypes.string,
  setSelectedImageId: PropTypes.func,
  removeImageBlot: PropTypes.func,
};

// Custom Blot
class CustomImageBlot extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    node.setAttribute("class", "fi-image-editor-container");
    node.setAttribute("data-id", value.id);

    // Render React component
    const root = createRoot(node);

    root.render(
      <ImageComponent
        src={value.src}
        alt={value.alt}
        id={value.id}
        setSelectedImageId={value.setSelectedImageId}
        removeImageBlot={value.removeImageBlot}
      />,
    );

    return node;
  }

  static value(...args) {
    const [node] = args;
    const imgElement = node.querySelector("img");

    return {
      src: imgElement?.src || "",
      alt: imgElement?.alt || "",
      id: node.getAttribute("data-id"),
    };
  }
}

CustomImageBlot.blotName = "customImage";
CustomImageBlot.tagName = "span";

export default CustomImageBlot;
