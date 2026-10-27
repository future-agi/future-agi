import PropTypes from "prop-types";
import GitHostForm from "./GitHostForm";

/** Code calling OpenAI, Anthropic or Gemini directly: the repository form, on any git host. */
export default function DirectApiForm(props) {
  return <GitHostForm {...props} direct />;
}
DirectApiForm.propTypes = {
  item: PropTypes.object.isRequired,
  onBusyChange: PropTypes.func,
};
