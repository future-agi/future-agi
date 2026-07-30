import React from "react";
import { Helmet } from "react-helmet-async";
import TrialDetail from "../../../sections/test-detail/TrialDetail/TrialDetail";

const TestExecutionOptimizationTrialDetail = () => {
  return (
    <>
      <Helmet>
        <title>Trial Detail</title>
      </Helmet>
      <TrialDetail />
    </>
  );
};

export default TestExecutionOptimizationTrialDetail;
