import React, { useState } from "react";
import PropTypes from "prop-types";
import { Badge, Box, IconButton, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import { grey } from "@mui/material/colors";

const TemplateRow = ({ template, idx, count, isOutlined }) => {
  const [internalExpanded, setInternalExpanded] = useState(
    idx === 1 ? true : false,
  );
  const [isEllipis, setIsEllipsis] = useState(false);
  return (
    <Box sx={{ gap: 1, display: "flex", flexDirection: "column" }}>
      <Box key={template} sx={{ display: "flex", gap: 3 }}>
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            width: "12px",
          }}
        >
          <Badge
            badgeContent={idx}
            sx={{
              "& .MuiBadge-badge": {
                backgroundColor: "action.hover",
              },
            }}
          />
        </Box>
        <Box
          sx={{
            width: "calc(100% - 30px)",
            backgroundColor: "background.paper",
            padding: "14px",
            borderRadius: "12px",
            display: "flex",
            alignItems: "flex-start",
            transition: "all 0.5s ease-in-out",
            overflow: "hidden",
            ...(isOutlined && {
              border: "1px solid",
              borderColor: "divider",
              backgroundColor: "transparent",
            }),
          }}
        >
          <Box
            sx={{
              flexGrow: 1,
              transition: "max-height 0.3s ease-in-out",
              maxHeight: internalExpanded ? "1000px" : "24px", // Adjust these values based on your content
              overflow: "hidden",
            }}
          >
            <Typography
              variant="subtitle2"
              fontWeight={400}
              noWrap={!internalExpanded}
              sx={{
                flexGrow: 1,
                overflow: "hidden",
                textOverflow: internalExpanded ? "unset" : "ellipsis",
                display: "block",
              }}
            >
              <div
                style={{
                  width: "570px",
                  overflow: internalExpanded ? "visible" : "hidden",
                  textOverflow: internalExpanded ? "unset" : "ellipsis",
                  display: "inline-block",
                  verticalAlign: "middle",
                  lineHeight: "1.5em",
                }}
              >
                <Markdown
                  rehypePlugins={[rehypeSanitize]}
                  components={{
                    p: ({ node, ...props }) => (
                      <span style={{ display: "inline" }} {...props} />
                    ),
                    a: ({ node, ...props }) => (
                      <a target="_blank" rel="noopener noreferrer" {...props} />
                    ),
                  }}
                >
                  {template}
                </Markdown>
              </div>
            </Typography>
          </Box>

          {internalExpanded && (
            <IconButton
              color="primary.grey"
              onClick={() => {
                isEllipis ? setIsEllipsis(false) : setIsEllipsis(true);
                copyToClipboard(template);
                enqueueSnackbar({
                  variant: "success",
                  message: "Prompt copied to clipboard",
                });
              }}
            >
              <Iconify icon="basil:copy-outline" />
            </IconButton>
          )}
          <IconButton onClick={() => setInternalExpanded(!internalExpanded)}>
            <Iconify
              icon={
                internalExpanded
                  ? "eva:arrow-ios-upward-fill"
                  : "eva:arrow-ios-downward-fill"
              }
              sx={{ color: grey }}
            />
          </IconButton>
        </Box>
      </Box>
      {idx === 1 && (
        <Typography
          sx={{ paddingLeft: 5 }}
          variant="caption"
          color="text.secondary"
        >
          Best template out of the {count}
        </Typography>
      )}
    </Box>
  );
};

TemplateRow.propTypes = {
  template: PropTypes.string,
  idx: PropTypes.number,
  count: PropTypes.number,
  isOutlined: PropTypes.bool,
};

const TemplateAccordion = ({ templates, mode }) => {
  const isOutlined = mode === "outlined";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: "14px" }}>
        {/* <Iconify icon="fluent:calendar-template-20-filled" /> */}
        <Typography fontWeight={500} fontSize="14px">
          {templates?.length > 1
            ? `Top ${templates?.length} Templates`
            : "Top Template"}
        </Typography>
      </Box>

      <Box
        sx={{
          borderRadius: "8px",
          boxShadow: "none",
          ...(isOutlined && {
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: "transparent",
          }),
        }}
      >
        <Box
          sx={{
            backgroundColor: isOutlined ? "transparent" : "background.default",
            padding: "16px",
            borderRadius: "8px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          {templates.map((template, index) => (
            <TemplateRow
              template={template}
              idx={index + 1}
              key={template}
              count={templates?.length || 0}
              isOutlined={isOutlined}
            />
          ))}
        </Box>
      </Box>
    </Box>
  );
};

TemplateAccordion.propTypes = {
  templates: PropTypes.array,
  mode: PropTypes.string,
};

export default TemplateAccordion;

{
  /* <IconButton color="primary">
<Iconify icon="basil:copy-outline" />
</IconButton> */
}
