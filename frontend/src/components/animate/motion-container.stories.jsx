import React from "react";
import { motion } from "framer-motion";
import MotionContainer from "./motion-container";

const meta = {
  component: MotionContainer,
  title: "Components/MotionContainer",
  argTypes: {
    animate: {
      control: "boolean",
      description: "Controls whether the container is in animate state",
    },
    action: {
      control: "boolean",
      description: "Determines the animation behavior",
    },
    children: {
      control: "text",
      description: "Content inside the motion container",
    },
  },
  decorators: [
    (Story) => (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "300px",
        }}
      >
        <Story />
      </div>
    ),
  ],
};

export default meta;

export const Default = {
  args: {
    children: (
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5 }}
        style={{
          padding: "20px",
          background: "lightblue",
          width: "200px",
          height: "100px",
        }}
      >
        Motion Container Content
      </motion.div>
    ),
  },
};

export const WithAnimation = {
  args: {
    animate: true,
    children: (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        style={{
          padding: "20px",
          background: "lightgreen",
          width: "200px",
          height: "100px",
        }}
      >
        Animated Motion Container
      </motion.div>
    ),
  },
};

export const ActionMode = {
  args: {
    action: true,
    animate: true,
    children: (
      <motion.div
        initial={{ opacity: 0, rotate: -10 }}
        animate={{ opacity: 1, rotate: 0 }}
        transition={{ duration: 0.5 }}
        style={{
          padding: "20px",
          background: "lightsalmon",
          width: "200px",
          height: "100px",
        }}
      >
        Action Mode Motion Container
      </motion.div>
    ),
  },
};
