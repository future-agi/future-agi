import { Box } from "@mui/material";
import React, { useCallback } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router";
import PageHeader from "src/components/page-header";

import TaskListView from "./TaskListView";

const EvalsTasksViewV2 = ({ observeId = null }) => {
  const navigate = useNavigate();

  const handleCreateTask = useCallback(() => {
    if (observeId) {
      navigate(`/dashboard/tasks/create?project=${observeId}`);
    } else {
      navigate("/dashboard/tasks/create");
    }
  }, [navigate, observeId]);

  const handleRowClick = useCallback(
    (row) => {
      if (row?.id) {
        navigate(`/dashboard/tasks/${row.id}`);
      }
    },
    [navigate],
  );

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        height: "100%",
        p: 2,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Header */}
      {!observeId && (
        <PageHeader
          title="Tasks"
          description="Create and run automated actions on your data"
          sx={{ mb: 0 }}
        />
      )}

      {/* Task List */}
      <Box sx={{ flex: 1, mt: observeId ? 0 : 1, minHeight: 0 }}>
        <TaskListView
          observeId={observeId}
          onCreateTask={handleCreateTask}
          onRowClick={handleRowClick}
        />
      </Box>
    </Box>
  );
};

EvalsTasksViewV2.propTypes = {
  observeId: PropTypes.string,
};

export default EvalsTasksViewV2;
