import PropTypes from "prop-types";
import React from "react";
import PageHeader from "src/components/page-header";

// Thin wrapper kept for existing call sites; renders the shared PageHeader.
const PageHeadings = ({ title, description }) => (
  <PageHeader title={title} description={description} sx={{ mb: 0 }} />
);

export default PageHeadings;

PageHeadings.propTypes = {
  title: PropTypes.string,
  description: PropTypes.any,
};
