import React from "react";
import PropTypes from "prop-types";
import { m, AnimatePresence } from "framer-motion";

import Stack from "@mui/material/Stack";
import { alpha } from "@mui/material/styles";
import IconButton from "@mui/material/IconButton";
import ListItemText from "@mui/material/ListItemText";

import { fData } from "src/utils/format-number";

import Iconify from "../iconify";
import { varFade } from "../animate";
import FileThumbnail, { fileData } from "../file-thumbnail";

// ----------------------------------------------------------------------

export default function MultiFilePreview({ thumbnail, files, onRemove, sx }) {
  return (
    <AnimatePresence initial={false}>
      {files?.map((file) => {
        const { key, name = "", size = 0 } = fileData(file);

        const isNotFormatFile = typeof file === "string";

        if (thumbnail) {
          return (
            <Stack
              key={key}
              component={m.div}
              {...varFade().inUp}
              alignItems="center"
              display="inline-flex"
              justifyContent="center"
              sx={{
                m: 0.5,
                width: 80,
                height: 80,
                borderRadius: "8px",
                overflow: "hidden",
                position: "relative",
                border: (theme) =>
                  `solid 1px ${alpha(theme.palette.text.disabled, 0.16)}`,
                ...sx,
              }}
            >
              <FileThumbnail
                tooltip
                imageView
                file={file}
                sx={{ position: "absolute" }}
                imgSx={{ position: "absolute" }}
              />

              {onRemove && (
                <IconButton
                  size="small"
                  onClick={() => onRemove(file)}
                  sx={{
                    top: 4,
                    right: 4,
                    position: "absolute",
                    color: "common.white",
                    bgcolor: (theme) => alpha(theme.palette.text.primary, 0.48),
                    "&:hover": {
                      bgcolor: (theme) =>
                        alpha(theme.palette.text.primary, 0.72),
                    },
                  }}
                >
                  <Iconify icon="solar:close-linear" width={14} />
                </IconButton>
              )}
            </Stack>
          );
        }

        return (
          <Stack
            key={key}
            component={m.div}
            {...varFade().inUp}
            spacing={1.5}
            direction="row"
            alignItems="center"
            sx={{
              my: 0.5,
              py: 1,
              px: 1.5,
              minHeight: 48,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "border.default",
              ...sx,
            }}
          >
            <FileThumbnail file={file} sx={{ width: 28, height: 28 }} />

            <ListItemText
              primary={isNotFormatFile ? file : name}
              secondary={isNotFormatFile ? "" : fData(size)}
              primaryTypographyProps={{
                fontSize: 13,
                fontWeight: 500,
                noWrap: true,
              }}
              secondaryTypographyProps={{
                component: "span",
                fontSize: 12,
                color: "text.tertiary",
              }}
              sx={{ my: 0 }}
            />

            {onRemove && (
              <IconButton size="small" onClick={() => onRemove(file)}>
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            )}
          </Stack>
        );
      })}
    </AnimatePresence>
  );
}

MultiFilePreview.propTypes = {
  files: PropTypes.array,
  onRemove: PropTypes.func,
  sx: PropTypes.object,
  thumbnail: PropTypes.bool,
};
