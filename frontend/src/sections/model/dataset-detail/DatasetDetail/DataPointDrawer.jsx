import {
  Box,
  Drawer,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useParams } from "src/routes/hooks";
import { format } from "date-fns";
import CircularProgressWithLabel from "src/components/circular-progress-with-label/CircularProgressWithLabel";
import {
  getScorePercentage,
  interpolateColorBasedOnScore,
} from "src/utils/utils";
import DetailItem from "src/components/drawer-detail-item/drawer-detail-item";
import Image from "src/components/image";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const DataPointDrawer = ({
  selectedRow,
  open,
  onClose,
  metricList,
  setSelectedImages,
}) => {
  const { dataset } = useParams();

  const onImageClick = (curUrl) => {
    const images = [];
    let defaultIdx = 0;
    selectedRow.input
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    selectedRow.output
      ?.filter((o) => o["url"] !== undefined)
      ?.forEach((url) => {
        if (curUrl === url.url) defaultIdx = images.length;
        images.push({ src: url.url });
      });
    setSelectedImages({ images, defaultIdx });
  };

  const renderTextOrImage = (contentType, content) => {
    if (contentType === "text") {
      return (
        <Typography key={content} variant="body2">
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

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "480px",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <Box sx={{ padding: "20px" }}>
        <Box>
          <Typography variant="body2" color="text.disabled">
            Datapoints info
          </Typography>
          <Iconify />
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 3.75,
            marginTop: 3.75,
          }}
        >
          <DetailItem
            title="Model Input"
            content={renderTextOrImage(
              selectedRow?.inputType,
              selectedRow?.input,
            )}
          />
          <DetailItem
            title="Model Output"
            content={renderTextOrImage(
              selectedRow?.outputType,
              selectedRow?.output,
            )}
          />
          <DetailItem
            title="Environment"
            content={
              <Typography variant="body2">
                {dataset?.split("-")?.[0]}
              </Typography>
            }
          />
          <DetailItem
            title="Version"
            content={
              <Typography variant="body2">
                {dataset?.split("-")?.[1]}
              </Typography>
            }
          />
          <DetailItem
            title="Date Created"
            content={
              <Typography variant="body2">
                {selectedRow?.dateCreated
                  ? format(new Date(selectedRow?.dateCreated), "yyyy-MM-dd")
                  : "-"}
              </Typography>
            }
          />
          {metricList?.length ? (
            <DetailItem
              title="Performance"
              content={
                <Box sx={{ width: "100%" }}>
                  <TableContainer sx={{ width: "100%" }} component={Paper}>
                    <Table>
                      <TableHead>
                        <TableRow>
                          <TableCell>Metric Name</TableCell>
                          <TableCell>Score</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {metricList.map(({ id, name }) => (
                          <TableRow key={id}>
                            <TableCell>{name}</TableCell>
                            <TableCell>
                              <CircularProgressWithLabel
                                color={interpolateColorBasedOnScore(
                                  selectedRow?.[id],
                                )}
                                value={getScorePercentage(selectedRow?.[id])}
                              />
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              }
            />
          ) : (
            <></>
          )}
        </Box>
      </Box>
    </Drawer>
  );
};

DataPointDrawer.propTypes = {
  selectedRow: PropTypes.object,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  metricList: PropTypes.array,
  setSelectedImages: PropTypes.func,
};

export default DataPointDrawer;
