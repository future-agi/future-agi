import { Component } from "react";
import PropTypes from "prop-types";

// A palette part that cannot render (a missing provider, a failed read) must
// never take the page down with it: it renders nothing instead.
export default class PaletteBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

PaletteBoundary.propTypes = { children: PropTypes.node };
