import React from "react";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router-dom";
import { isPrototypeMode } from "src/utils/prototype";
import LensLab from "src/sections/common/Lenses/LensLab";

/**
 * `/dashboard/develop/r4-lab` (prototype only, not in the nav): lane M's
 * lens lab (`?lens=call&row=<rowId>&runs=<ids>`) and F's logo check
 * (`?logos=1`). Outside prototype mode it sends you to the dataset list.
 */
export default function R4Lab() {
  if (!isPrototypeMode()) return <Navigate to="/dashboard/develop" replace />;
  return (
    <>
      <Helmet>
        <title>R4 lab</title>
      </Helmet>
      <LensLab />
    </>
  );
}
