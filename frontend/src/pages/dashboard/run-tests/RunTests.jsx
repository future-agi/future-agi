import { Box } from "@mui/material";
import React, { useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import CreateRunTestPage from "src/components/run-tests/CreateRunTestPage";
import RunTestsContent from "src/components/run-tests/RunTestsContent";

function RunTests() {
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const gridRef = useRef(null);

  const handleCreateSuccess = () => {
    setCreateDialogOpen(false);
    gridRef.current?.api?.refreshServerSide({ purge: true });
  };

  return (
    <>
      <Helmet>
        <title>Run Simulation | Future AGI</title>
      </Helmet>

      {createDialogOpen ? (
        <CreateRunTestPage
          open={createDialogOpen}
          onClose={handleCreateSuccess}
        />
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100vh",
            p: 2,
          }}
        >
          <RunTestsContent
            showHeader={true}
            showSearch={true}
            onCreateClick={() => setCreateDialogOpen(true)}
            gridRef={gridRef}
          />
        </Box>
      )}
    </>
  );
}

export default RunTests;
