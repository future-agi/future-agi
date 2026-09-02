import PropTypes from "prop-types";
import VectorStoreForm from "./VectorStoreForm";

const ChromaForm = (props) => <VectorStoreForm {...props} provider="Chroma" />;

ChromaForm.propTypes = { allColumns: PropTypes.array, control: PropTypes.object };

export default ChromaForm;
