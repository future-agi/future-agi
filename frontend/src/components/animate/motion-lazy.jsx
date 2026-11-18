import React from "react";
import PropTypes from "prop-types";
import { m, LazyMotion } from "framer-motion";

// Keep the full feature bundle off the static app entry graph.
const loadFeatures = () =>
  import("./motion-features").then((module) => module.default);

// ----------------------------------------------------------------------

export function MotionLazy({ children }) {
  return (
    <LazyMotion strict features={loadFeatures}>
      <m.div style={{ height: "100%" }}> {children} </m.div>
    </LazyMotion>
  );
}

MotionLazy.propTypes = {
  children: PropTypes.node,
};
