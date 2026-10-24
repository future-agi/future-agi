import PropTypes from "prop-types";
import { Box } from "@mui/material";
import ListToolbar from "src/components/list-toolbar";
import PageHeader from "src/components/page-header";
import AnnotationsTabs from "./annotations-tabs";

// One frame for every Annotations tab (Queues, Labels): same header, tab
// block, toolbar wrapper and content gutter, so switching tabs moves nothing.
// Rhythm: title → tabs 12px, tabs → toolbar 12px, toolbar → table 8px.
export default function AnnotationsPageLayout({
  title,
  description,
  headerActions,
  toolbar,
  children,
}) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "background.paper",
      }}
    >
      <Box sx={{ px: 3, pt: 2, flexShrink: 0 }}>
        <PageHeader
          title={title}
          description={description}
          actions={headerActions}
          sx={{ mb: 0 }}
        />
      </Box>

      <Box sx={{ flexShrink: 0, px: 3, mt: 1.5 }}>
        <AnnotationsTabs />
      </Box>

      <ListToolbar sx={{ px: 3 }}>{toolbar}</ListToolbar>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
          px: 3,
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

AnnotationsPageLayout.propTypes = {
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  headerActions: PropTypes.node,
  toolbar: PropTypes.node,
  children: PropTypes.node,
};
