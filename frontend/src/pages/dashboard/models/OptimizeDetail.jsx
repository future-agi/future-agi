import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router";
import { useGetOptimization } from "src/api/model/optimize";
import OptimizeDetailView from "src/sections/model/optimize-detail/OptimizeDetailView";
import { LoadingScreen } from "src/components/loading-screen";

const OptimizeDetail = () => {
  const { optimizeId, id } = useParams();

  const { data, isPending } = useGetOptimization(id, optimizeId);

  if (isPending) return <LoadingScreen />;

  return (
    <>
      <Helmet>
        <title>{data?.name}</title>
      </Helmet>
      <OptimizeDetailView selectedOptimization={data} />
    </>
  );
};

export default OptimizeDetail;
