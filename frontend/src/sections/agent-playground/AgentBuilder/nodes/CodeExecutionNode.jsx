import React, { memo } from "react";
import PropTypes from "prop-types";
import BaseNode from "./BaseNode";
import { NODE_TYPES } from "../../utils/constants";

const CodeExecutionNode = ({ id, data, isConnectable, selected }) => {
  return (
    <BaseNode
      id={id}
      data={data}
      isConnectable={isConnectable}
      selected={selected}
      type={NODE_TYPES.CODE_EXECUTION}
    />
  );
};

CodeExecutionNode.propTypes = {
  id: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  isConnectable: PropTypes.bool,
  selected: PropTypes.bool,
};

const MemoizedCodeExecutionNode = memo(CodeExecutionNode);

export default MemoizedCodeExecutionNode;
