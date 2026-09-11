import React from "react";
import { Helmet } from "react-helmet-async";
import HuggingFaceView from "src/sections/huggingface/HuggingFaceView";

const HuggingFacePage = () => {
  return (
    <>
      <Helmet>
        <title>Dashboard: Hugging Face</title>
      </Helmet>
      <HuggingFaceView />
    </>
  );
};

export default HuggingFacePage;
