import { Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { metadataIconMapper, metaDataLabelMapper } from "./common";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { canonicalKeys } from "src/utils/utils";

export default function MetadataContent({ metadata = {} }) {
  // De-dupe mixed-key local metadata so each metric renders once with the
  // canonical label from `metaDataLabelMapper`.
  return (
    <Stack
      direction={"column"}
      sx={{
        overflow: "auto",
        paddingY: 2,
        mb: 3,
      }}
    >
      {canonicalKeys(metadata).map((key) => (
        <Stack key={key} direction={"row"} justifyContent={"space-between"}>
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
            sx={{
              flexShrink: 0,
            }}
          >
            {metaDataLabelMapper?.[key] ?? ""}:
          </Typography>
          <Stack direction={"row"} alignItems={"center"} gap={0.75}>
            <ShowComponent condition={metadataIconMapper?.[key]}>
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                  color: "text.primary",
                }}
                src={metadataIconMapper[key]}
              />
            </ShowComponent>
            <Typography
              typography={"s1"}
              sx={{
                textAlign: "right",
              }}
              color={"text.primary"}
            >
              {metadata[key] ?? "-"}
            </Typography>
          </Stack>
        </Stack>
      ))}
    </Stack>
  );
}

MetadataContent.propTypes = {
  metadata: PropTypes.object,
};
