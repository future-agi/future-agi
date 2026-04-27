import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Typography } from "@mui/material";
import Image from "src/components/image";
import _ from "lodash";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import {
  getPerformanceTagColor,
  getTabLabel,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import DetailItem from "./DetailItem";

const getScorePercentage = (s) => {
  if (s <= 0) s = 0;
  return s * 10;
};

const PromptTemplateEvalDetails = ({ performanceDetails, onImageClick }) => {
  const renderTextOrImage = (contentType, content) => {
    if (contentType === "text") {
      return (
        <Typography
          sx={{ wordBreak: "break-all" }}
          key={content}
          variant="body2"
        >
          {content}
        </Typography>
      );
    } else {
      const textContent = content?.filter(
        (obj) => obj["image"] === undefined,
      )?.[0]?.text;
      const images = content?.filter((obj) => obj["text"] === undefined);
      return (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Box key={content} sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            {images?.map(({ url }) => (
              <Image
                height={130}
                key={url}
                src={url}
                style={{ cursor: "pointer", borderRadius: "8px" }}
                onClick={() => onImageClick(url)}
              />
            ))}
          </Box>
          <Typography variant="body2">{textContent}</Typography>
        </Box>
      );
    }
  };

  const renderPastChats = (contentType, info) => {
    if (contentType === "text") {
      return (
        <Box>
          <Typography variant="body2" fontWeight={600} component="span">
            {_.capitalize(info.author.role)} {": "}
          </Typography>
          <Typography
            variant="body2"
            component="span"
            sx={{ wordBreak: "break-all" }}
          >
            {info.content}
          </Typography>
        </Box>
      );
    } else {
      const textContent = info?.content?.filter(
        (obj) => obj["image"] === undefined,
      )?.[0]?.text;
      const images = info?.content?.filter((obj) => obj["text"] === undefined);
      return (
        <Box>
          <Typography variant="body2" fontWeight={600} component="span">
            {_.capitalize(info.author.role)}
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            {images?.map(({ url }) => (
              <Image
                height={130}
                key={url}
                src={url}
                style={{ cursor: "pointer", borderRadius: "8px" }}
              />
            ))}
          </Box>
          <Typography variant="body2">{textContent}</Typography>
        </Box>
      );
    }
  };

  const renderVariables = () => {
    if (performanceDetails?.variables) {
      return Object.entries(performanceDetails?.variables).map(
        ([key, value]) => (
          <DetailItem
            key={key}
            title={key}
            content={<Typography variant="body2">{value}</Typography>}
          />
        ),
      );
    }

    return <></>;
  };

  return (
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
            {performanceDetails?.pastInput?.length ? (
              <Typography variant="body2" fontWeight={600} component="span">
                User :
              </Typography>
            ) : (
              <></>
            )}
            {renderTextOrImage(
              performanceDetails?.modelInputType,
              performanceDetails?.modelInput,
            )}
          </>
        }
      />

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
      <DetailItem
        title="Context"
        content={
          <Typography variant="body2">{performanceDetails?.context}</Typography>
        }
      />
      <DetailItem
        title="Prompt Template"
        content={
          <Typography variant="body2">
            {performanceDetails?.promptTemplate
              ? performanceDetails?.promptTemplate
              : "-"}
          </Typography>
        }
      />
      {renderVariables()}
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
  );
};

PromptTemplateEvalDetails.propTypes = {
  performanceDetails: PropTypes.object,
  onImageClick: PropTypes.func,
};

export default PromptTemplateEvalDetails;
