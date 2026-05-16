import PropTypes from "prop-types";
import React from "react";
import NumberValueSelector from "./NumberValueSelector";
import TextValueSelector from "./TextValueSelector";
import OptionValueSelector from "./OptionValueSelector";
import DateValueSelector from "./DateValueSelector";
import NumberValueOptionSelector from "./NumberValueOptionSelector";
import BooleanValueSelector from "./BooleanValueSelector";
import GenericOperator from "./GenericOperator";
import { ShowComponent } from "src/components/show/ShowComponent";
import { NULL_OPERATORS } from "../common";
import DevelopTextFilter from "src/sections/evals/DevelopFilters/DevelopTextFilter";
import AutocompleteTextValueSelector from "./AutocompleteTextValueSelector";

const ValueSelector = ({ definition, filter, updateFilter }) => {
  // Return null if filter_type is not defined yet
  if (!definition?.filterType?.type) {
    return null;
  }

  if (definition?.filterType?.type === "number") {
    if (definition.filterType.options) {
      return (
        <NumberValueOptionSelector
          definition={definition}
          filter={filter}
          updateFilter={updateFilter}
        />
      );
    }
    return (
      <NumberValueSelector
        definition={definition}
        filter={filter}
        updateFilter={updateFilter}
      />
    );
  } else if (definition?.filterType?.type === "text") {
    if (definition?.asyncOptions) {
      return (
        <AutocompleteTextValueSelector
          definition={definition}
          filter={filter}
          updateFilter={updateFilter}
        />
      );
    }
    if (definition?.showOperator) {
      return <DevelopTextFilter filter={filter} updateFilter={updateFilter} />;
    }
    return (
      <>
        <ShowComponent
          condition={!NULL_OPERATORS.includes(filter?.filter_config?.filter_op)}
        >
          <TextValueSelector
            definition={definition}
            filter={filter}
            updateFilter={updateFilter}
          />
        </ShowComponent>
      </>
    );
  } else if (definition?.filterType?.type === "option") {
    return (
      <OptionValueSelector
        definition={definition}
        filter={filter}
        updateFilter={updateFilter}
      />
    );
  } else if (definition?.filterType?.type === "date") {
    return (
      <DateValueSelector
        definition={definition}
        filter={filter}
        updateFilter={updateFilter}
      />
    );
  } else if (definition?.filterType?.type === "boolean") {
    return (
      <>
        {definition?.showOperator && (
          <GenericOperator
            definition={definition}
            filter={filter}
            updateFilter={updateFilter}
          />
        )}
        <BooleanValueSelector
          definition={definition}
          filter={filter}
          updateFilter={updateFilter}
        />
      </>
    );
  }
  return null;
};

ValueSelector.propTypes = {
  definition: PropTypes.object,
  filter: PropTypes.object,
  updateFilter: PropTypes.func,
};

export default ValueSelector;
