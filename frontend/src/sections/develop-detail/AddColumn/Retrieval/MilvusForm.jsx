import PropTypes from "prop-types";
import VectorStoreForm from "./VectorStoreForm";

const MilvusForm = (props) => <VectorStoreForm {...props} provider="Milvus" requiresApiKey />;

MilvusForm.propTypes = { allColumns: PropTypes.array, control: PropTypes.object };

export default MilvusForm;
