import React from "react";
import { Helmet } from "react-helmet-async";
import GetStartedView from "src/sections/get-started/GetStartedView";

const GetStarted = () => {
  return (
    <>
      <Helmet>
        <title>Get started · Future AGI</title>
      </Helmet>
      <GetStartedView />
    </>
  );
};

export default GetStarted;
