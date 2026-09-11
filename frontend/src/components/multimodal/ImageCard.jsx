import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import Image from "src/components/image";
import { useSingleImageViewContext } from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageContext";

const ImageCard = ({ image, width = 200 }) => {
  const { setImageUrl } = useSingleImageViewContext();
  return (
    <Box
      sx={{
        borderRadius: (theme) => theme.spacing(1),
        overflow: "hidden",
        cursor: "pointer",
      }}
      onClick={() => setImageUrl(image)}
    >
      <Image key={image} src={image} alt="image" width={width} />
    </Box>
  );
};

ImageCard.propTypes = {
  image: PropTypes.string,
  width: PropTypes.number,
};

export default ImageCard;
