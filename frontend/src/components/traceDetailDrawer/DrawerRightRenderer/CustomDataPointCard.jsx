import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Tab,
  Tabs,
  Typography,
  IconButton,
  useTheme,
} from "@mui/material";
import { ShowComponent } from "src/components/show";
import "react-json-view-lite/dist/index.css";
import { transformSafeJson } from "src/utils/utils";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import CellMarkdown from "src/sections/common/CellMarkdown";
import CustomVideoPlayer from "src/components/VideoPlayer/CustomVideoPlayer";
import TestAudioPlayer from "../../custom-audio/TestAudioPlayer";
import ImageCard from "src/components/multimodal/ImageCard";

const CustomDataPointCard = ({ value, label, allowCopy = false }) => {
  const [tabValue, setTabValue] = useState("raw");
  const theme = useTheme();

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  const isJson = (v) => {
    const finalString = transformSafeJson(v);
    try {
      JSON.parse(finalString);
      return true;
    } catch (e) {
      return false;
    }
  };
  // Removing this dataType check for now as always the datatype is text only
  // const formattedValue = useMemo(() => {
  //   if (dataType === "float") {
  //     return `${getScorePercentage(parseFloat(value?.cellValue) * 10)}%`;
  //   }
  //   return value?.cellValue;
  // }, [value?.cellValue, dataType]);

  const { texts, images, audios, videos } = useMemo(() => {
    const texts = [];
    const images = [];
    const audios = [];
    const videos = [];

    if (typeof value?.cellValue === "string") {
      return { texts: [value?.cellValue], images: [], audios: [], video: [] };
    }

    value?.cellValue?.forEach((item) => {
      if (typeof item === "string") {
        return texts.push(item);
      }
      if (item.type === "text") {
        texts.push(item.text);
      } else if (item.type === "image") {
        images.push(item.image);
      } else if (item.type === "audio") {
        audios.push(item.audio);
      } else if (item.type === "video") {
        videos.push({
          src: item.video,
          thumbnail: item["video.thumbnail"],
        });
      }
    });

    return { texts, images, audios, videos };
  }, [value?.cellValue]);

  return (
    <Box
      sx={{
        paddingBottom: (theme) => theme.spacing(1),
        border: "1px solid",
        borderColor: "action.selected",
        borderRadius: (theme) => theme.spacing(1),
        backgroundColor: "background.paper",
      }}
    >
      <Box>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            px: (theme) => theme.spacing(2),
          }}
        >
          <Typography
            variant="body2"
            sx={{
              zIndex: 2,
              fontWeight: "fontWeightMedium",
              color: "text.primary",
            }}
          >
            {label}
          </Typography>
          <Tabs
            textColor="primary"
            value={tabValue}
            onChange={handleTabChange}
            TabIndicatorProps={{
              style: { display: "none" },
            }}
            sx={{
              "& .MuiTab-root": {
                marginRight: (theme) => theme.spacing(2),
                color: "text.disabled",
                typography: "s2",
                fontWeight: "fontWeightRegular",
              },
              "& .Mui-selected": {
                color: theme.palette.primary.main,
                fontWeight: "fontWeightSemiBold",
              },
            }}
          >
            <Tab value="markdown" label="Markdown" />
            <Tab value="raw" label="Raw" />
            {allowCopy && (
              <IconButton
                onClick={() => {
                  copyToClipboard(texts.join("\n"));
                  enqueueSnackbar("Copied to clipboard", {
                    variant: "success",
                  });
                }}
              >
                <Iconify
                  icon="basil:copy-outline"
                  color="text.secondary"
                  width={20}
                />
              </IconButton>
            )}
          </Tabs>
        </Box>

        <ShowComponent condition={tabValue === "raw"}>
          <Box
            sx={{
              paddingX: (theme) => theme.spacing(2),
              paddingY: (theme) => theme.spacing(1.5),
              borderTop: "1px solid",
              borderColor: "divider",
              display: "flex",
              flexDirection: "column",
              gap: (theme) => theme.spacing(1),
            }}
          >
            {texts.map((text) => {
              if (isJson(text)) {
                return (
                  <CustomJsonViewer
                    key={text}
                    object={JSON.parse(transformSafeJson(text))}
                  />
                );
              }
              return (
                <Typography
                  key={text}
                  typography="s1"
                  sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                >
                  {text}
                </Typography>
              );
            })}
            <ShowComponent condition={images.length > 0}>
              <SingleImageViewerProvider>
                <Box
                  sx={{
                    display: "flex",
                    gap: (theme) => theme.spacing(1),
                    flexWrap: "wrap",
                  }}
                >
                  {images.map((image) => (
                    <ImageCard key={image} image={image} />
                  ))}
                </Box>
              </SingleImageViewerProvider>
            </ShowComponent>

            <ShowComponent condition={audios.length > 0}>
              <AudioPlaybackProvider>
                {audios.map((url) => (
                  <TestAudioPlayer
                    key={url}
                    audioData={{
                      url,
                    }}
                  />
                ))}
              </AudioPlaybackProvider>
            </ShowComponent>
            <ShowComponent condition={videos?.length > 0}>
              <Box
                sx={{
                  display: "flex",
                  gap: (theme) => theme.spacing(1),
                  flexWrap: "wrap",
                }}
              >
                {videos?.map((video, idx) => (
                  <CustomVideoPlayer
                    key={idx}
                    videoSrc={video.src}
                    thumbnail={video.thumbnail}
                  />
                ))}
              </Box>
            </ShowComponent>
          </Box>
        </ShowComponent>

        <ShowComponent condition={tabValue === "markdown"}>
          <Box
            sx={{
              paddingX: (theme) => theme.spacing(2),
              paddingY: (theme) => theme.spacing(1.5),
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              variant="body2"
              sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {texts.map((text) => {
                if (isJson(text)) {
                  return (
                    <CustomJsonViewer
                      key={text}
                      object={JSON.parse(transformSafeJson(text))}
                    />
                  );
                }
                return <CellMarkdown key={text} text={text} spacing={0} />;
              })}
              <ShowComponent condition={images.length > 0}>
                <Box
                  sx={{
                    display: "flex",
                    gap: (theme) => theme.spacing(1),
                    flexWrap: "wrap",
                  }}
                >
                  {images.map((image) => (
                    <ImageCard key={image} image={image} />
                  ))}
                </Box>
              </ShowComponent>
              <ShowComponent condition={videos?.length > 0}>
                <Box
                  sx={{
                    display: "flex",
                    gap: (theme) => theme.spacing(1),
                    flexWrap: "wrap",
                  }}
                >
                  {videos?.map((video, idx) => (
                    <CustomVideoPlayer
                      key={idx}
                      videoSrc={video.src}
                      thumbnail={video.thumbnail}
                    />
                  ))}
                </Box>
              </ShowComponent>
            </Typography>
            <ShowComponent condition={images.length > 0}>
              <SingleImageViewerProvider>
                <Box
                  sx={{
                    display: "flex",
                    gap: (theme) => theme.spacing(1),
                    flexWrap: "wrap",
                  }}
                >
                  {images.map((image) => (
                    <ImageCard key={image} image={image} />
                  ))}
                </Box>
              </SingleImageViewerProvider>
            </ShowComponent>

            <ShowComponent condition={audios.length > 0}>
              <AudioPlaybackProvider>
                {audios.map((url) => (
                  <TestAudioPlayer
                    key={url}
                    audioData={{
                      url,
                    }}
                  />
                ))}
              </AudioPlaybackProvider>
            </ShowComponent>
            <ShowComponent condition={videos?.length > 0}>
              <Box
                sx={{
                  display: "flex",
                  gap: (theme) => theme.spacing(1),
                  flexWrap: "wrap",
                }}
              >
                {videos?.map((video, idx) => (
                  <CustomVideoPlayer
                    key={idx}
                    videoSrc={video.src}
                    thumbnail={video.thumbnail}
                  />
                ))}
              </Box>
            </ShowComponent>
          </Box>
        </ShowComponent>
      </Box>
    </Box>
  );
};

CustomDataPointCard.propTypes = {
  value: PropTypes.object,
  label: PropTypes.string,
  allowCopy: PropTypes.bool,
};

export default CustomDataPointCard;
