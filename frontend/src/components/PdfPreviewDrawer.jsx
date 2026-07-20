import {
  Box,
  Drawer,
  Typography,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  Button,
} from "@mui/material";
import React, { useState, useEffect, useRef } from "react";
import { Viewer, Worker } from "@react-pdf-viewer/core";
import { defaultLayoutPlugin } from "@react-pdf-viewer/default-layout";
import { usePdfPreviewStoreShallow } from "src/utils/CommonStores/pdfPreviewStore";
import Iconify from "./iconify";
import SvgColor from "./svg-color";

import "@react-pdf-viewer/core/lib/styles/index.css";
import "@react-pdf-viewer/default-layout/lib/styles/index.css";
import logger from "src/utils/logger";
import { getFileIcon } from "src/sections/knowledge-base/sheet-view/icons";
import { errorMessages } from "./common";

const isLocalUrl = (url) => {
  try {
    if (!url) return false;
    const parsed = new URL(url);
    const hostname = parsed.hostname;
    return (
      hostname === "localhost" ||
      hostname === "127.0.0.1" ||
      hostname.startsWith("192.168.") ||
      hostname.startsWith("10.") ||
      hostname.endsWith(".local")
    );
  } catch (e) {
    return false;
  }
};

const PdfPreviewDrawer = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [textContent, setTextContent] = useState("");
  const { openPdfPreviewDrawer, closePreview } = usePdfPreviewStoreShallow(
    (state) => ({
      openPdfPreviewDrawer: state.openPdfPreviewDrawer,
      closePreview: state.closePreview,
    }),
  );

  const fileUrl = openPdfPreviewDrawer?.url;
  const name = openPdfPreviewDrawer?.name;
  const type = openPdfPreviewDrawer?.type;
  const isPublic = openPdfPreviewDrawer?.isPublic;

  const fetchedUrlsRef = useRef();

  const handleCustomDownload = async () => {
    try {
      const response = await fetch(fileUrl);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name || "download";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      logger.error("Download failed:", error);
      window.open(fileUrl, "_blank");
    }
  };

  const defaultLayoutPluginInstance = defaultLayoutPlugin({
    renderToolbar: () => null,
    sidebarTabs: () => [],
  });

  useEffect(() => {
    if (!fileUrl) return;

    if (fetchedUrlsRef.current === fileUrl) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    if (type?.toLowerCase() === "txt" || type?.toLowerCase() === "text") {
      fetchedUrlsRef.current = fileUrl;
      fetchTextContent();
    } else if (["png", "jpg", "jpeg", "webp", "gif", "svg", "bmp"].includes(type?.toLowerCase())) {
      // wait for image component to load
    } else {
      setLoading(false);
    }
  }, [fileUrl, type]);

  useEffect(() => {
    return () => {
      fetchedUrlsRef.current = null;
      closePreview?.();
    };
  }, []);

  const fetchTextContent = async () => {
    try {
      const response = await fetch(fileUrl);
      if (!response.ok) {
        setError(errorMessages.notAccessible);
        setLoading(false);
        return;
      }
      const text = await response.text();
      setTextContent(text);
      setLoading(false);
    } catch (err) {
      logger.error("Failed to fetch text:", err);
      setError(errorMessages.networkError);
      setLoading(false);
    }
  };

  const renderPreview = () => {
    if (!fileUrl) return null;

    const lowerType = type?.toLowerCase();

    switch (lowerType) {
      case "pdf":
        return (
          <Box sx={{ height: "100%", backgroundColor: "background.paper" }}>
            <Worker workerUrl="https://unpkg.com/pdfjs-dist@3.11.174/build/pdf.worker.min.js">
              <Viewer
                fileUrl={fileUrl}
                plugins={[defaultLayoutPluginInstance]}
                theme="light"
                onDocumentLoad={() => setLoading(false)}
                renderError={() => {
                  setError("Failed to load Resource");
                }}
              />
            </Worker>
          </Box>
        );

      case "txt":
      case "text":
        return (
          <Box
            sx={{
              height: "100%",
              overflow: "auto",
              backgroundColor: "background.neutral",
              p: 3,
            }}
          >
            <Paper
              elevation={2}
              sx={{
                width: "100%",
                maxWidth: 900,
                margin: "0 auto",
                p: 4,
                backgroundColor: "background.paper",
                fontFamily: "monospace",
                fontSize: "14px",
                lineHeight: 1.8,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                overflowWrap: "break-word",
              }}
            >
              {textContent || "No content to display"}
            </Paper>
          </Box>
        );

      case "doc":
      case "docx":
        if (!isPublic) {
          setError(errorMessages.notAccessible);
          return null;
        }

        if (isLocalUrl(fileUrl)) {
          return (
            <Box
              sx={{
                height: "100%",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                backgroundColor: "background.neutral",
                p: 3,
              }}
            >
              <Paper
                elevation={3}
                sx={{
                  p: 5,
                  maxWidth: 500,
                  width: "100%",
                  textAlign: "center",
                  borderRadius: 2,
                  bgcolor: "background.paper",
                }}
              >
                <Box sx={{ mb: 3, display: "flex", justifyContent: "center" }}>
                  <Box
                    component="img"
                    src={getFileIcon("docx")}
                    alt="Word Document"
                    sx={{ width: 80, height: 80 }}
                  />
                </Box>
                <Typography variant="h5" sx={{ mb: 1.5, fontWeight: "fontWeightBold" }}>
                  Local Preview Unavailable
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 4 }}>
                  Office document previews (.docx / .doc) rely on Microsoft&apos;s cloud viewer, which cannot access files hosted on your local system (localhost).
                  <br />
                  <br />
                  Please download the file to view it on your computer.
                </Typography>
                <Button
                  variant="contained"
                  color="primary"
                  size="large"
                  onClick={handleCustomDownload}
                  startIcon={
                    <SvgColor
                      src="/assets/icons/action_buttons/ic_download.svg"
                      sx={{ width: 20, height: 20, color: "common.white" }}
                    />
                  }
                  sx={{
                    px: 4,
                    py: 1.5,
                    borderRadius: 1,
                    textTransform: "none",
                    fontWeight: "fontWeightBold",
                    boxShadow: (theme) => theme.customShadows?.z8,
                  }}
                >
                  Download Document
                </Button>
              </Paper>
            </Box>
          );
        }

        return (
          <div style={{ position: "relative", height: "100%" }}>
            <iframe
              src={`https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(fileUrl)}`}
              style={{ width: "100%", height: "100%", border: "none" }}
            />

            <div
              style={{
                position: "absolute",
                bottom: 0,
                left: 0,
                right: 0,
                height: "30px",
                background: "var(--bg-paper)",
                pointerEvents: "none",
              }}
            />
          </div>
        );

      case "png":
      case "jpg":
      case "jpeg":
      case "webp":
      case "gif":
      case "svg":
      case "bmp":
        return (
          <Box
            sx={{
              height: "100%",
              width: "100%",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              backgroundColor: "background.neutral",
              p: 2,
              overflow: "auto",
            }}
          >
            <Box
              component="img"
              src={fileUrl}
              alt={name || "Image Preview"}
              sx={{
                maxWidth: "100%",
                maxHeight: "100%",
                objectFit: "contain",
                borderRadius: "8px",
                boxShadow: (theme) => theme.customShadows?.z24 || theme.shadows[24],
              }}
              onLoad={() => setLoading(false)}
              onError={() => {
                setLoading(false);
                setError("Failed to load image");
              }}
            />
          </Box>
        );

      default:
        setError(errorMessages.formatNotSupported);
        return null;
    }
  };

  return (
    <Drawer
      open={Boolean(openPdfPreviewDrawer)}
      onClose={closePreview}
      anchor="right"
      sx={{
        "& .MuiDrawer-paper": {
          width: { xs: "100%", sm: 900, md: 1000 },
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <Box
        height="100%"
        display="flex"
        flexDirection="column"
        bgcolor="background.paper"
      >
        <Box
          p={2}
          borderBottom="1px solid var(--border-default)"
          display="flex"
          justifyContent="space-between"
          alignItems="center"
          bgcolor="background.paper"
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "flex-start",
              alignItems: "center",
              gap: 1.5,
            }}
          >
            <Box
              component={"img"}
              sx={{
                height: "20px",
                width: "20px",
              }}
              alt="document icon"
              src={getFileIcon(type, "pdf")}
            />
            <Typography fontWeight={600} noWrap sx={{ flex: 1, mr: 2 }}>
              {`${name || "File Preview"}${
                name && !name.endsWith(type) ? `.${type}` : ""
              }`}
            </Typography>
          </Box>

          <Box display="flex" gap={1} alignItems="center">
            <IconButton
              size="small"
              onClick={handleCustomDownload}
              title="Download file"
            >
              <SvgColor
                src="/assets/icons/action_buttons/ic_download.svg"
                sx={{
                  width: 20,
                  height: 20,
                  color: "text.primary",
                }}
              />
            </IconButton>

            <IconButton size="small" onClick={closePreview} title="Close">
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Box>
        </Box>

        <Box flex={1} position="relative" overflow="hidden">
          {loading && (
            <Box
              position="absolute"
              top={0}
              left={0}
              right={0}
              bottom={0}
              display="flex"
              justifyContent="center"
              alignItems="center"
              bgcolor="background.paper"
              zIndex={1}
            >
              <Box textAlign="center">
                <CircularProgress />
                <Typography variant="body2" color="text.secondary" mt={2}>
                  Loading preview...
                </Typography>
              </Box>
            </Box>
          )}

          {error && !loading && (
            <Box
              position="absolute"
              top={0}
              left={0}
              right={0}
              bottom={0}
              display="flex"
              justifyContent="center"
              alignItems="center"
              bgcolor="background.paper"
              zIndex={1}
            >
              <Stack
                spacing={2}
                display="flex"
                flexDirection="column"
                alignItems="center"
                justifyContent="center"
              >
                <Box
                  sx={{
                    borderRadius: "100%",

                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <img
                    src="/assets/errorfallback/something_went_wrong.svg"
                    alt=""
                    style={{
                      width: "200px",
                      height: "200px",
                    }}
                  />
                </Box>

                <Stack
                  spacing={0.25}
                  textAlign="center"
                  display={"flex"}
                  alignItems={"center"}
                >
                  <Typography variant="m3" fontWeight="fontWeightMedium">
                    Unable to preview this document
                  </Typography>

                  <Typography variant="s1" fontWeight="fontWeightRegular">
                    We couldn’t load the document preview. The file may be
                    unsupported, unavailable, or temporarily inaccessible.
                  </Typography>
                </Stack>
              </Stack>
            </Box>
          )}

          <Box height="100%">{renderPreview()}</Box>
        </Box>
      </Box>
    </Drawer>
  );
};

export default PdfPreviewDrawer;
