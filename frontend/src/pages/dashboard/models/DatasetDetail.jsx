import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "src/routes/hooks";
import DatasetDetailView from "src/sections/model/dataset-detail/DatasetDetailView";
import { DatasetContextProvider } from "./DatasetContext";

const DatasetDetail = () => {
  const { dataset } = useParams();

  return (
    <>
      <Helmet>
        <title>{dataset}</title>
      </Helmet>
      <DatasetContextProvider>
        <DatasetDetailView />
      </DatasetContextProvider>
    </>
  );
};

export default DatasetDetail;
