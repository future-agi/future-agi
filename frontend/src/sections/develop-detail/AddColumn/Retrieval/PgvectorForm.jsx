import PropTypes from "prop-types";
import VectorStoreForm from "./VectorStoreForm";

const PgvectorForm = (props) => <VectorStoreForm {...props} provider="pgvector" />;

PgvectorForm.propTypes = { allColumns: PropTypes.array, control: PropTypes.object };

export default PgvectorForm;
