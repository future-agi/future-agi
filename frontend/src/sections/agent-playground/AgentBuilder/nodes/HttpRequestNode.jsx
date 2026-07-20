import React, { memo } from "react";
import PropTypes from "prop-types";
import BaseNode from "./BaseNode";

const HttpRequestNode = ({ id, data, isConnectable, selected }) => {
  return (
    <BaseNode
      id={id}
      type="http_request"
      data={data}
      isConnectable={isConnectable}
      selected={selected}
    />
  );
};

HttpRequestNode.propTypes = {
  id: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  isConnectable: PropTypes.bool,
  selected: PropTypes.bool,
};

export default memo(HttpRequestNode);
