import { Box } from "@mui/material";
import React from "react";
import { PYTHON_DOCUMENTATION_URL } from "src/utils/constant";
import { useNavigate } from "react-router";

import ConnectorRow from "./ConnectorRow";

const ConnectorView = () => {
  // let [searchParams, setSearchParam] = useSearchParams({
  //   selectedConnector: null,
  // });

  const navigate = useNavigate();

  // const setSelectedConnector = (v) => setSearchParam({ selectedConnector: v });

  // const selectedConnector = searchParams.s/electedConnector;

  // if (selectedConnector === "bigQuery") {
  //   return (
  //     <BigQueryWizard
  //       onClose={() => {
  //         queryClient.removeQueries({ queryKey: ["draftId"], type: "all" });
  //         setSearchParam({ selectedConnector: null, draftId: null });
  //       }}
  //     />
  //   );
  // } else if (selectedConnector === "uploadFile") {
  //   return (
  //     <UploadFileWizard
  //       onClose={() => {
  //         queryClient.removeQueries({ queryKey: ["draftId"], type: "all" });
  //         setSearchParam({ selectedConnector: null, draftId: null });
  //       }}
  //     />
  //   );
  // }

  return (
    <Box sx={{ display: "flex", flexDirection: "column" }}>
      <ConnectorRow
        title="Upload Data"
        connectors={[
          {
            title: "Upload CSV",
            icon: "/assets/icons/connectors/c_upload.svg",
            onClick: () => navigate("/dashboard/sync/connectors/upload-file"),
          },
        ]}
      />
      <ConnectorRow
        title="Connect Table"
        connectors={[
          {
            title: "Big Query",
            icon: "/assets/icons/connectors/c_big_query.svg",
            onClick: () => navigate("/dashboard/sync/connectors/big-query"),
          },
          {
            title: "Databricks (Coming Soon)",
            icon: "/assets/icons/connectors/c_data_bricks.svg",
          },
          {
            title: "Snowflake (Coming Soon)",
            icon: "/assets/icons/connectors/c_snowflaks.svg",
          },
        ]}
      />
      <ConnectorRow
        title="Use SDK"
        connectors={[
          {
            title: "Python Realtime",
            icon: "/assets/icons/connectors/c_python.svg",
            onClick: () => window.open(PYTHON_DOCUMENTATION_URL),
          },
        ]}
      />
    </Box>
  );
};

export default ConnectorView;
