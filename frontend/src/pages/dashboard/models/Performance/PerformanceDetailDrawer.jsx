import { Box, Chip, Drawer, IconButton, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Image from "src/components/image";
import Iconify from "src/components/iconify";
import {
  getPerformanceTagColor,
  getTabLabel,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import _ from "lodash";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";

const getScorePercentage = (s) => {
  if (s <= 0) s = 0;
  return s * 10;
};

const PerformanceDetailDrawer = ({
  open,
  onClose,
  performanceDetails,
  isContextEval,
}) => {
  const renderTextOrImage = (contentType, content) => {
    return contentType === "text" ? (
      <Typography key={content} variant="body2">
        {content}
      </Typography>
    ) : (
      <Box key={content} sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
        {content?.map(({ url }) => (
          <Image
            height={130}
            key={url}
            src={url}
            style={{ cursor: "pointer", borderRadius: "8px" }}
          />
        ))}
      </Box>
    );
  };

  const renderPastChats = (contentType, info) => {
    return contentType === "text" ? (
      <>
        <Typography variant="body2" fontWeight={600} component="span">
          {_.capitalize(info.author.role)}
        </Typography>
        <Typography variant="body2" component="span">
          {info.content}
        </Typography>
      </>
    ) : (
      <>
        <Typography variant="body2" fontWeight={600} component="span">
          {_.capitalize(info.author.role)}
        </Typography>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
          {info.content?.map(({ url }) => (
            <Image
              height={130}
              key={url}
              src={url}
              style={{ cursor: "pointer", borderRadius: "8px" }}
            />
          ))}
        </Box>
      </>
    );
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "80vh",
          width: "550px",
          position: "fixed",
          zIndex: 9999,
          top: "10%",
          right: 30,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
    >
      <IconButton
        onClick={() => onClose()}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="mingcute:close-line" />
      </IconButton>
      <Box
        sx={{
          paddingY: 3.75,
          paddingX: 2,
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
        }}
      >
        <DetailItem
          title="Model Input"
          content={
            <>
              {performanceDetails?.pastInput?.map((inp) =>
                renderPastChats(performanceDetails?.modelInputType, inp),
              )}
              {renderTextOrImage(
                performanceDetails?.modelInputType,
                performanceDetails?.modelInput,
              )}
            </>
          }
        />
        {isContextEval ? (
          <DetailItem
            title="Context"
            content={
              <Typography variant="body2">
                {performanceDetails?.context}
              </Typography>
            }
          />
        ) : (
          <DetailItem
            title="Model Output"
            content={
              <>
                {renderTextOrImage(
                  performanceDetails?.modelOutputType,
                  performanceDetails?.modelOutput,
                )}
              </>
            }
          />
        )}

        <DetailItem
          title="Score"
          content={
            <CircularProgressWithLabel
              color={interpolateColorBasedOnScore(performanceDetails?.score)}
              value={getScorePercentage(performanceDetails?.score)}
            />
          }
        />
        <DetailItem
          title="Explanation"
          content={
            <Typography variant="body2">
              {performanceDetails?.explanation}
            </Typography>
          }
        />
        <DetailItem
          title="Tags"
          content={
            <Box sx={{ display: "inline-flex", gap: 1, flexWrap: "wrap" }}>
              {performanceDetails?.tags?.map((tag) => {
                const color = getPerformanceTagColor(tag);
                return (
                  <Chip
                    variant="soft"
                    size="small"
                    color={color}
                    key={tag}
                    label={getTabLabel(tag)}
                  />
                );
              })}
            </Box>
          }
        />
      </Box>
    </Drawer>
  );
};

const DetailItem = ({ title, content }) => {
  return (
    <Box sx={{ display: "flex", gap: "12px" }}>
      <Box>
        <Iconify
          icon="solar:double-alt-arrow-right-bold-duotone"
          color="primary.main"
          width={24}
        />
      </Box>
      <Box sx={{ gap: 1, display: "flex", flexDirection: "column" }}>
        <Box>
          <Typography fontSize={14} fontWeight={700} color="text.disabled">
            {title}
          </Typography>
        </Box>
        <Box>{content}</Box>
      </Box>
    </Box>
  );
};

DetailItem.propTypes = {
  title: PropTypes.string,
  content: PropTypes.any,
};

PerformanceDetailDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  performanceDetails: PropTypes.object,
  isContextEval: PropTypes.bool,
};

export default PerformanceDetailDrawer;
