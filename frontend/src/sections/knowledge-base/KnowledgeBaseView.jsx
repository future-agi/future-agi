import { Box, Button } from "@mui/material";
import React, { useRef, useState } from "react";
import PageHeadings from "../develop-detail/Common/PageHeadings";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { handleOnDocsClicked } from "src/utils/Mixpanel";
import InfoKnowledgeModal from "./help/InfoKnowledgeModal";
import CreateKnowledgeBaseDrawer from "./CreateKnowledgeBase/CreateKnowledgeBaseDrawer";
import KnowledgeBaseData from "./KnowledgeBaseData/KnowledgeBaseData";

const KnowledgeBaseView = () => {
  const { role } = useAuthContext();
  const canCreate = RolePermission.KNOWLEDGE_BASE[PERMISSIONS.CREATE][role];
  const [createKnowledgeBase, setCreateKnowledgeBase] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [hasData, setHasData] = useState(true);
  const gridRef = useRef();

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 3,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <PageHeadings
          title="Knowledge base"
          description="Domain documents your agents and synthetic data are grounded in"
        />
      </Box>
      <Box sx={{ display: hasData ? "block" : "none", flex: 1 }}>
        <KnowledgeBaseData
          setHasData={setHasData}
          setShowHelp={setShowHelp}
          setCreateKnowledgeBase={setCreateKnowledgeBase}
          ref={gridRef}
        />
      </Box>
      <Box sx={{ display: hasData ? "none" : "block", flex: 1 }}>
        <EmptyState
          icon="solar:notebook-linear"
          title="No knowledge bases yet"
          description="Upload your documents once, then use them to generate grounded synthetic data and to ground your agents."
          steps={[
            {
              title: "Upload documents",
              description:
                "PDF, DOCX, TXT or RTF, here or with the SDK.",
            },
            {
              title: "Wait for processing",
              description: "Each file's status updates live as it's indexed.",
            },
            {
              title: "Use it",
              description:
                "Pick the knowledge base when you create synthetic data.",
            },
          ]}
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              disabled={!canCreate}
              onClick={() => canCreate && setCreateKnowledgeBase(true)}
            >
              Create knowledge base
            </Button>
          }
          docsHref="https://docs.futureagi.com/docs/knowledge-base"
          onDocsClick={() => handleOnDocsClicked("knowledge_base_empty")}
        />
      </Box>
      <CreateKnowledgeBaseDrawer
        open={createKnowledgeBase}
        onClose={() => setCreateKnowledgeBase(false)}
        setHasData={setHasData}
        refreshGrid={() => gridRef?.current?.api?.refreshServerSide({})}
      />
      <InfoKnowledgeModal
        open={showHelp && !createKnowledgeBase}
        onClose={() => setShowHelp(false)}
        onCreateKnowledge={() => {
          setCreateKnowledgeBase(true);
          setShowHelp(false);
        }}
      />
    </Box>
  );
};

export default KnowledgeBaseView;
