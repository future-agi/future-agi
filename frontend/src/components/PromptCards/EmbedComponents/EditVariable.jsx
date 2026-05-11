import SVGColor from "src/components/svg-color";
import PropTypes from "prop-types";

// This component is embedded inside quill editor on run time and can't access theme easily
// hence we need to hardcode the colors and other styles here.
const EditVariable = ({ openVariableEditor, fromBlock = false }) => {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
      }}
    >
      {!fromBlock && (
        <span
          style={{
            color: "var(--mention-invalid-color)",
            fontWeight: "500",
            fontSize: "15px",
            backgroundColor: "var(--mention-invalid-bg)",
          }}
        >{`}`}</span>
      )}
      <span className="edit-variable-button" onClick={openVariableEditor}>
        <SVGColor
          src="/assets/icons/components/ic_edit.svg"
          sx={{ width: "14px", height: "14px", color: "var(--text-muted)" }}
        />
      </span>
    </span>
  );
};

EditVariable.propTypes = {
  openVariableEditor: PropTypes.func,
  fromBlock: PropTypes.bool,
};

export default EditVariable;
