import React from "react";
import { Helmet } from "react-helmet-async";
import OptimizationDetail from "../../../sections/test-detail/OptimizationDetail/OptimizationDetail";

const TestExecutionOptimizationDetail = () => {
  return (
    <>
      <Helmet>
        <title>Optimization Detail</title>
      </Helmet>
      <OptimizationDetail />
    </>
  );
};

export default TestExecutionOptimizationDetail;
