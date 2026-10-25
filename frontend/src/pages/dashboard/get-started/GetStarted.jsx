import React from "react";
import { Helmet } from "react-helmet-async";
import GetStartedLaunchView from "src/sections/get-started/GetStartedLaunchView";

const GetStarted = () => {
  return (
    <>
      <Helmet>
        <title>Get started with FutureAGI</title>
      </Helmet>
      <GetStartedLaunchView />
    </>
  );
};

export default GetStarted;
