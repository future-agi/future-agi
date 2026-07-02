import { Box, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import { copyToClipboard } from "src/utils/utils";
import logger from "src/utils/logger";

const RenderMeta = ({ meta, originType, showToken = true, valuesInfo }) => {
  if (!["run_prompt", "optimisation", "text"].includes(originType)) {
    return <></>;
  }
  if (!meta || !Object.values(meta).find((value) => value)) {
    return <></>;
  }

  logger.debug({ originType, a: meta });

  return (
    <Box
      className="render-meta"
      sx={{ display: "flex", justifyContent: "space-between" }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 2,
          lineHeight: "1.5",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
          <Iconify
            icon="material-symbols:schedule-outline"
            width="15px"
            height="15px"
            color="text.secondary"
          />
          <Typography typography="s3" color="text.secondary">
            {meta?.responseTimeMs
              ? `${Math.round(meta?.responseTimeMs)} ms`
              : "-"}
          </Typography>
        </Box>
        <ShowComponent condition={showToken}>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
            <SvgColor
              src={`/assets/icons/components/ic_stack_coins.svg`}
              sx={{ width: "15px", height: "15px", color: "text.secondary" }}
            />
            <Typography typography="s3" color="text.secondary">
              {meta?.tokenCount ? `${meta?.tokenCount}` : "-"}
            </Typography>
          </Box>
        </ShowComponent>
        <Box sx={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <Iconify
            icon="solar:dollar-linear"
            width="15px"
            height="15px"
            color="text.secondary"
          />
          <Typography typography="s3" color="text.secondary">
            {originType == "text"
              ? meta?.cost?.total_cost
              : valuesInfo?.metadata
                ? `${valuesInfo?.metadata?.cost?.total_cost}`
                : "-"}
          </Typography>
        </Box>
      </Box>

      <Box sx={{ display: "flex", alignItems: "center" }}>
        <Iconify
          icon="basil:copy-outline"
          width="14px"
          height="14px"
          color="text.disabled"
          onClick={() => {
            copyToClipboard(valuesInfo?.reason);
            enqueueSnackbar("Copied to clipboard", {
              variant: "success",
            });
          }}
          sx={{
            cursor: "pointer",
          }}
        />
      </Box>
    </Box>
  );
};

RenderMeta.propTypes = {
  meta: PropTypes.object,
  originType: PropTypes.string,
  showToken: PropTypes.bool,
  valuesInfo: PropTypes.any,
};

export default RenderMeta;
