err_dict = {
    "PROMPT_TEMPLATE_ID_REQUIRED": ["Prompt template ID is required."],
    "PROMPT_TEMPLATE_NOT_FOUND": ["Prompt template not found."],
    "METRIC_VALID_CONTEXT": ["Context is required in the data for evaluation."],
    "METRIC_VALID_TEMPLATE": ["Template is required in the data for evaluation."],
    "METRIC_VALID_CONTENT": ["Output is required in the data for evaluation."],
    "MODEL_NAME_IS_USED": [
        "Model name already exists with a different model type. Please use a different name."
    ],
    "INVALID_CONNETOR_TYPE": ["The specified connector type is invalid."],
    "INVALID_CONNECTOR_ID": ["The specified connector ID is invalid."],
    "UNSUPPORTED_FILE": ["The file type is not supported."],
    "ERROR_IMAGE_UPLOAD": ["Failed to upload image to S3: {}"],
    "ERROR_DOCUMENT_UPLOAD": ["Failed to upload document to S3: {}"],
    "ERROR_FILE_UPLOAD": ["Failed to upload file to S3: {}"],
    "INVALID_URL": ["The provided URL is invalid or inaccessible."],
    "INVALID_SOURCE_SELECTION": ["The selected source is invalid."],
    "INVALID_BASE64_STRING": ["The provided base64 string is invalid or corrupted."],
    "UNIQUE_CONVERSATION_ID": ["Conversation ID must be unique in the data."],
    "INPUT_DATA_MISMATCHED": [
        "The provided data structure does not match the existing model requirements."
    ],
    "MAPPING_COLUMN_MISMATCH": [
        "The column mapping values do not match the existing data structure."
    ],
    "TAGS_MISMATCH": ["The provided tags do not match the column data structure."],
    "NO_COLUMN_TO_FETCH": ["No columns available for the specified table ID."],
    "VALID_CONNECTOR": [
        "Please use valid connector types: Knowledge Base (kb) or Data Points (dp)."
    ],
    "TEST_CONNECTION_FAILED": [
        "Connection test failed. Please contact support for assistance."
    ],
    "INCORRECT_TABLE_FORMAT": [
        "Invalid table ID format. Required format: project_id.dataset_id.table_id"
    ],
    "USER_ORGANIZATION_CONNECTION_ERROR": [
        "User is not associated with any organization. Please verify organization access."
    ],
    "INVALID_VALUE_OF_is_active": [
        "Invalid status value. Please use 'true' or 'false'."
    ],
    "USER_LIMIT_REACHED": [
        "Organization user limit reached. Please upgrade your plan to add more users."
    ],
    "EMAIL_ALREADY_EXIST": ["An account with this email address already exists."],
    "MEMBER_ID_NOT_MENTIONED": ["Member ID is required for this operation."],
    "USER_CANNOT_REMOVE_ORG": [
        "You cannot remove yourself from your current organization."
    ],
    "WRONG_USERS_DETAILS": [
        "Invalid user details. Please check email, organization role, and name."
    ],
    "IN_VALID_CONNECTION_ID": ["The specified connection ID is invalid."],
    "IN_VALID_CONNECTION_STATUS": ["The specified connection status is invalid."],
    "IN_VALID_CONNECTOR_TYPE": ["The specified connector type is invalid."],
    "FAILED_TO_RETRIEVE_DRAFT": ["Unable to retrieve the draft. Please try again."],
    "INVALID_UPDATE_DETAILS": ["The provided update details are invalid."],
    "INVALID_CONNECTION_DETAILS": ["The provided connection details are invalid."],
    "NO_FILE_UPLOADED": ["No file was provided for upload."],
    "IN_VALID_METRICS": ["The specified metric is invalid."],
    "ANNOTATION_CREATION_FAILED": ["Failed to create annotation. Please try again."],
    "FAILED_TO_ADD_ANNOTATIONS": ["Unable to add annotations. Please try again."],
    "ANNOTATION_ID_NOT_EXIST": ["The specified annotation ID does not exist."],
    "ANNOTATION_ID_REQUIRED": ["Annotation ID is required for this operation."],
    "LABLE_VALUES_OR_RESPONSE_FIELD_VALUES_MISSING": [
        "Either label values or response field values must be provided."
    ],
    "ROW_ID_LABEL_ID_AND_VALUE_REQUIRED_IN_LABLE_UPDATE": [
        "Label updates require row ID, label ID, and value."
    ],
    "ROW_ID_LABEL_ID_AND_VALUE_REQUIRED_IN_RESPONSE_UPDATE": [
        "Response field updates require row ID, label ID, and value."
    ],
    "ROW_ID_COLUMN_ID_AND_VALUE_REQUIRED": [
        "Updates require row ID, column ID, and value."
    ],
    "ROW_ID_MISSING": ["Row ID is required for this operation."],
    "COLUMNS_IDS_MISSING": ["Required column IDs are missing from column mapping."],
    "ROW_NOT_EXIST": ["The specified row ID does not exist."],
    "ROW_ORDER_MISSING": ["Row order is required for this operation."],
    "DATASET_ID_MISSING": ["Dataset ID is required for this operation."],
    "STATIC_OR_RESPONSE_COLUMN_MISSING": [
        "At least one static or response column must be provided."
    ],
    "ORGANIZATION_ID_MISSING": ["Organization ID is required for this operation."],
    "MISSING_DATASET_ID": ["Dataset ID is required for this operation."],
    "MISSSING_EVAL_IDS": ["Evaluation IDs are required for this operation."],
    "DATASET_PATH_MISSING": ["Dataset path is required for this operation."],
    "DATASET_CREATE_LIMIT_REACHED": [
        "Dataset creation limit has been reached for your plan."
    ],
    "DATASET_NAME_MISSING": ["Dataset name is required for Hugging Face datasets."],
    "DATASET_EXIST_IN_ORG": [
        "A dataset with this name already exists in your organization."
    ],
    "SOURCE_TARGET_DATASET_ARE_SAME": [
        "Source and target datasets cannot be the same."
    ],
    "MISSING_SOURCE_DATASET_ID_AND_COLUMN_MAPPINGS": [
        "Source dataset ID and column mapping are required."
    ],
    "UNSUPPORTED_FILE_FORMAT": [
        "Unsupported file format. Please use: .csv, .xlsx, .json, or .jsonl files."
    ],
    "10_ROWS_REQUIRED": ["A minimum of 10 rows is required for data generation."],
    "INVALID_SOURCE": ["The specified source is invalid."],
    "MISSING_COLUMN_DATA_AS_DICT": [
        "Column data must be provided as a list of dictionaries."
    ],
    "MISSING_COLUMN_NAME_AND_TYPE": ["Column name and type are required fields."],
    "MISSING_ROW_IDS": ["Row IDs are required for this operation."],
    "ROWS_NOT_POSITIVE": ["Number of rows must be greater than zero."],
    "INVALID_ROWS_NUM": ["Invalid number of rows specified."],
    "DATA_MISSING": ["Data is required for insertion."],
    "NEW_COLUMN_NAME_MISSING": ["New column name is required."],
    "INVALID_COLUMN_IDS": ["One or more column IDs are invalid."],
    "INVALID_BOOLEAN_VALUE": ["Invalid boolean value. Please use true or false."],
    "MISSING_ROW_ID_COLUMN_ID_AND_NEW_VALUE": [
        "Row ID, column ID, and new value are required."
    ],
    "INVALID_INTEGER": ["The provided value must be a valid integer."],
    "INVALID_FLOAT_VALUE": ["The provided value must be a valid decimal number."],
    "INVALID_DATETIME_VALUE": ["The provided value must be a valid date and time."],
    "INVALID_ARRAY": ["Invalid array format. Array must begin with '['."],
    "INVALID_ARRAY_VALUE": ["The provided array value is invalid."],
    "INVALID_JSON_FORMAT": ["Invalid JSON format. Object must begin with '{'."],
    "INVALID_JSON_VALUE": ["The provided JSON value is invalid."],
    "MISSING_NEW_COLUMN_TYPE": ["New column type is required."],
    "INVALID_DATA_TYPE": ["The specified data type is invalid."],
    "INVALID_OR_MISSING_EVAL_TYPE": [
        "Invalid evaluation type. Must be one of: preset, user, previously_configured",
        "Invalid evaluation type. Must be one of: preset, user",
    ],
    "EVALS_NOT_FOUND": ["No matching evaluations found."],
    "FAILED_TO_FETCH_DATASET": [
        "Dataset unavailable - request access by visiting https://huggingface.co/datasets/ and update your Hugging Face token on our platform."
    ],
    "MISSING_COLUMN_ID_AND_JSON_KEY": ["Column ID and JSON key are required."],
    "CONCURRENCY_NOT_POSITIVE": ["Concurrency value must be greater than zero."],
    "CONCURRENCY_EXCEEDS_MAX": ["Concurrency value must not exceed 10."],
    "CONCURRENCY_INVALID": ["Concurrency value must be a valid integer."],
    "MISSING_COLUMN_ID_AND_LABELS": ["Column ID and labels are required."],
    "LABELS_LIST_NOT_VALID": ["Labels must contain at least 2 items."],
    "MISSING_COLUMN_ID_AND_INSTRUCTIONS": ["Column ID and instruction are required."],
    "MISSING_COLUMN_NAME_AND_CONFIG": ["Column name and configuration are required."],
    "CODE_MISSING": ["Code is required for this operation."],
    "CONFIG_MISSING": ["Configuration is required."],
    "MISSING_COLUMN_ID_SUB_TYPE_AND_API_KEY": [
        "Column ID, subtype, and API key are required."
    ],
    "MISSING_USER_EVAL_METRIC_ID": [
        "User evaluation metric ID is invalid or not found."
    ],
    "USER_EVAL_METRIC_ID_REQUIRED": ["User evaluation metric ID is required."],
    "USER_EVAL_METRIC_IDs_REQUIRED": ["User evaluation metric IDs are required."],
    "USER_EVAL_METRIC_NOT_EXIST": [
        "The specified user evaluation metric does not exist."
    ],
    "MISSING_METRIC_ID_FEEDBACK_ID_AND_ACTION_TYPE": [
        "Action type, user evaluation metric ID, and feedback ID are required."
    ],
    "NUM_COPIES_NOT_POSITIVE": ["Number of copies must be greater than zero."],
    "MISSING_NEW_DATASET_NAME": ["New dataset name is required."],
    "MISSING_SOURCE_DATASET_ID": ["Source dataset ID is required."],
    "AT_LEAST_2_DATSET_IDS": ["At least two dataset IDs must be provided."],
    "COMMON_COLUMNS_NOT_FOUND": ["No common columns found between datasets."],
    "FAILED_TO_FETCH_DATASETS": ["Unable to fetch datasets list. Please try again."],
    "FAILED_TO_FETCH_TRACE_LIST": ["Unable to fetch traces list. Please try again."],
    "MISSING_COLUMN_ID": ["Column ID is required."],
    "MISSING_METRIC_ID": ["Metric ID is required."],
    "MISSING_METRIC_IDS": ["Metric IDs are required."],
    "INVALID_METRIC_IDS": ["One or more metric IDs are invalid."],
    "INVALID_PAGINATION": ["Invalid pagination parameters provided."],
    "EXPERIMENT_NOT_FOUND": ["The specified experiment was not found."],
    "EXPERIMENT_DATASET_NOT_FOUND": ["No experiment datasets found."],
    "MISSING_EXP_IDS": ["Experiment IDs are required."],
    "MISSING_PROPERTY_NAME": ["Property name is required."],
    "DATASET_NOT_FOUND_FOR_INSIGHT": ["No dataset found for this insight."],
    "MISSING_PROMPT": ["Prompt is required."],
    "TEMPLATE_ALREADY_EXIST": ["A template with this name already exists."],
    "MISSING_STATEMENT": ["Statement is required for prompt generation."],
    "EXISTING_PROMTP_REQUIRED": ["Existing prompt is required for prompt generation."],
    "MISSING_IMPROVEMENT_REQUIREMENTS": [
        "Improvement requirements are required for prompt generation."
    ],
    "MISSING_EXPLANATION": ["Explanation is required."],
    "MISSING_PROMPT_NAME_AND_VARIABLE_NAME": [
        "Prompt name and variable names are required."
    ],
    "MISSING_REQUIRED_FIELDS": [
        "Required fields missing: dataset_id or column_placeholders."
    ],
    "DATASET_NOT_FOUND": ["The specified dataset does not exist or has been deleted."],
    "ROW_INDICES_NOT_EXIST": ["One or more row indices do not exist."],
    "COLUMN_IS_IN_VALID": ["The specified column is not a valid run prompt column."],
    "PROVIDER_MISSING": ["Provider information is required."],
    "RUN_PROMPTS_IDS_MISSING": ["Run prompt IDs are required."],
    "MISSING_EVAL_TEMPLATE": ["Evaluation template not found."],
    "INVALID_JSON": ["Invalid JSON format provided."],
    "INVALID_SUBSCRIPTION_TYPE": ["The specified subscription type is invalid."],
    "FAILED_TO_CREATE_CHECKOUT_SESSION": [
        "Unable to create checkout session. Please try again."
    ],
    "INVALID_EVENT_DATA": ["The provided event data is invalid."],
    "INVALID_PAYMENT_TYPE": ["The specified payment type is invalid."],
    "FAILED_TO_CANCEL_SUBS": ["Unable to cancel the subscription. Please try again."],
    "COLUMN_DELETED": ["Column has been deleted in this metric: "],
    "ONLY_OWNER_CAN_VIEW_TEAMS": ["Only organization owners can view team members."],
    "NOT_AUTHORIZED_TO_UPDATE": [
        "You do not have permission to update this annotation."
    ],
    "NOT_AUTHORIZED_TO_RESET": ["You do not have permission to reset this annotation."],
    "FAILED_TO_RESET_ANNOTATION": ["Unable to reset the annotation. Please try again."],
    "CANNOT_ANNOTATE_LABEL": [
        "You do not have permission to annotate this label for row: "
    ],
    "CREATE_CONNECTION_FAILED": ["Unable to create connection. Please try again."],
    "DELETE_CONNECTION_FAILED": ["Unable to delete connection. Please try again."],
    "UPDATE_CONNECTION_FAILED": ["Unable to update connection. Please try again."],
    "LOADING_DATA_FAILED": ["Unable to load data. Please try again."],
    "DRAFT_CREATION_FAILED": ["Unable to create draft. Please try again."],
    "ANNOTATION_LABEL_CREATION_FAILED": [
        "Unable to create annotation label. Please try again."
    ],
    "ANNOTATION_LABEL_UPDATION_FAILED": [
        "Unable to update annotation label. Please try again."
    ],
    "ANNOTATION_LABEL_DELETION_FAILED": [
        "Unable to delete annotation label. Please try again."
    ],
    "FAILED_LOADING_ANNOTATION": ["Unable to fetch annotation. Please try again."],
    "FAILED_TO_UPDATE_ANNOTATION": ["Unable to update annotation. Please try again."],
    "FAILED_TO_DELETE_ANNOTATION": ["Unable to delete annotation. Please try again."],
    "FAILED_TO_GENERATE_ANNOTATION": [
        "Unable to generate annotation. Please try again."
    ],
    "DELETE_OPERATION_FAILED": ["Delete operation failed. Please try again."],
    "CELLS_UPDATION_FAILED": ["Unable to update cells. Please try again."],
    "FAILED_TO_ADD_RUN_PROMPT_COLUMN": [
        "Unable to add run prompt column. Please try again."
    ],
    "FAILED_TO_PREVIEW_RUN_PROMPT_COLUMN": [
        "Unable to preview run prompt column. Please try again."
    ],
    "FAILED_TO_UPDATE_RUN_PROMPT_COLUMN": [
        "Unable to update run prompt column. Please try again."
    ],
    "FAILED_TO_RETRIEVE_RUN_PROMPT_COLUMN": [
        "Unable to retrieve run prompt column. Please try again."
    ],
    "FAILED_TO_RETRIEVE_PROVIDER_CONFIG": [
        "Unable to retrieve default provider configuration for your organization."
    ],
    "SET_PROVIDER_AS_DEFAULT_FAILED": [
        "Unable to set provider as default for your organization."
    ],
    "FAILED_TO_GET_RUN_PROMPT_OPTIONS": [
        "Unable to retrieve run prompt options. Please try again."
    ],
    "FAILED_TO_RUN_PROMPT_ON_ROWS": ["Unable to run prompt on rows. Please try again."],
    "FAILED_TO_GENERATE_PROMPT": ["Unable to generate prompt. Please try again."],
    "FAILED_TO_IMPROVE_PROMPT": ["Unable to improve prompt. Please try again."],
    "FAILED_TO_GENERATE_SDK_CODE": ["Unable to generate SDK code. Please try again."],
    "FAILED_TO_ANALYSED_PROMPT": ["Unable to analyze prompt. Please try again."],
    "FAILED_TO_GET_RUN_PROMPT_DATA": [
        "Unable to retrieve run prompt details. Please try again."
    ],
    "FAILED_TO_RUN_STANDALONE_EVAL": [
        "Unable to run standalone evaluation. Please try again."
    ],
    "FAILED_CREATION_OBSERVATION_SPAN": [
        "Unable to create observation span. Please try again."
    ],
    "FAILED_TO_CREATE_OBS_SPAN_BULK": [
        "Unable to create observation spans in bulk. Please try again."
    ],
    "FAILED_GET_OBSERVATION_SPAN": [
        "Unable to retrieve observation span. Please try again."
    ],
    "FAILED_TO_CREATE_SUBSCRIPTION": [
        "Unable to create subscription. Please try again."
    ],
    "FAILED_TO_GET_SUBSCRIPTION": [
        "Unable to retrieve subscription. Please try again."
    ],
    "FAILED_TO_UPDATE_SUBSCRIPTION": [
        "Unable to update subscription details. Please try again."
    ],
    "FAILED_TO_GET_ORG_BILLING": [
        "Unable to retrieve organization billing data. Please try again."
    ],
    "FAILED_TO_UPDATE_BILLING": ["Unable to update billing data. Please try again."],
    "FAILED_TO_GET_ORG_SUBSCRIPTION": [
        "Unable to retrieve organization subscription. Please try again."
    ],
    "FAILED_TO_UPDATE_ORG_SUBSCRIPTION": [
        "Unable to update organization subscription. Please try again."
    ],
    "FAILED_TO_DELETE_ORG_SUBSCRIPTION": [
        "Unable to delete organization subscription. Please try again."
    ],
    "FAILED_TO_CREATE_ORG_SUBSCRIPTION": [
        "Unable to create organization subscription. Please try again."
    ],
    "FAILED_TO_GET_ORG": ["Unable to retrieve organizations. Please try again."],
    "FAILED_TO_FETCH_RATE_LIMIT": ["Unable to fetch rate limit. Please try again."],
    "FAILED_TO_CREATE_RATE_LIMIT": ["Unable to create rate limit. Please try again."],
    "FAILED_TO_UPDATE_RATE_LIMIT": ["Unable to update rate limit. Please try again."],
    "FAILED_TO_DELETE_RATE_LIMIT": ["Unable to delete rate limit. Please try again."],
    "FAILED_TO_GET_PRICING": ["Unable to retrieve pricing details. Please try again."],
    "FAILED_TO_SAVE_PRICING_DATA": [
        "Unable to save pricing details. Please try again."
    ],
    "FAILED_TO_DELETE_PRICING": ["Unable to delete pricing data. Please try again."],
    "FAILED_TO_UPDATE_PRICING": ["Unable to update pricing details. Please try again."],
    "FAILED_TO_GET_CALL_TYPES": [
        "Unable to retrieve API call types. Please try again."
    ],
    "FAILED_TO_GET_RESOURCE_TYPES": [
        "Unable to retrieve resource types. Please try again."
    ],
    "FAILED_TO_GET_RESOURCE_LIMIT": [
        "Unable to retrieve resource limits. Please try again."
    ],
    "FAILED_TO_SAVE_RESOURCE_LIMIT": [
        "Unable to save resource limit. Please try again."
    ],
    "FAILED_TO_UPDATE_RESOURCE_LIMIT": [
        "Unable to update resource limit. Please try again."
    ],
    "FAILED_TO_DELETE_RESOURCE_LIMIT": [
        "Unable to delete resource limit. Please try again."
    ],
    "FAILED_TO_CREATE_USER_EVAL_TEMP": [
        "Unable to create user evaluation template. Please try again."
    ],
    "FAILED_TO_PROCESS_ROW": [
        "Unable to process row. Please try again or contact Future AGI support."
    ],
    "FAILED_TO_PROCESS_EVALUATION": [
        "Evaluation failed. Please contact Future AGI support."
    ],
    "FAILED_TO_PROCESS_PROTECT_EVALUATION": [
        "Unable to process protect evaluation right now. Please try again later."
    ],
    "FAILED_TO_CREATE_SUBS_CHECKOUT_SESSION": [
        "Unable to create subscription checkout session. Please try again."
    ],
    "CREATE_CUSTOM_PAYMENT_SESSION_FAILED": [
        "Unable to create custom payment checkout session. Please try again."
    ],
    "FAILED_TO_CREATE_AUTO_RECHARGE_SESSION": [
        "Unable to create auto-recharge session. Please try again."
    ],
    "STRIPE_WEBHOOK_OPERATION_FAILED": [
        "Stripe webhook operation failed. Please try again."
    ],
    "FAILED_TO_GET_LAST_FOUR_DIGITS": [
        "Unable to retrieve last 4 digits of credit card. Please try again."
    ],
    "FAILED_TO_UPDATE_AUTO_RELOAD_SETTINGS": [
        "Unable to update auto-reload settings. Please try again."
    ],
    "FAILED_TO_GET_AUTO_RELOAD_SETTINGS": [
        "Unable to retrieve auto-reload settings. Please try again."
    ],
    "FAILED_TO_GET_SUBS_DETAILS": [
        "Unable to retrieve current subscription details. Please try again."
    ],
    "FAILED_TO_GET_PRICING_CARD_DATAILS": [
        "Unable to retrieve pricing card details. Please try again."
    ],
    "FAILED_TO_GET_ORG_WALLET_DATA": [
        "Unable to retrieve organization wallet balance. Please try again."
    ],
    "FAILED_TO_GET_CUSTOMER_INVOICE_DATA": [
        "Unable to retrieve customer invoice details. Please try again."
    ],
    "FAILED_TO_DOWNLAOD_INVOICE": ["Unable to download invoice PDF. Please try again."],
    "INVOICE_CUSTOMER_CONNECTION_FAILED": [
        "This invoice does not belong to your account."
    ],
    "FAILED_TO_GET_ORG_BILLING_DATA": [
        "Unable to retrieve organization billing details. Please try again."
    ],
    "FAILED_TO_UPDATE_BILLING_DATA": [
        "Unable to update organization billing details. Please try again."
    ],
    "FAILED_TO_GET_TOKENS": ["Unable to retrieve tokens. Please try again."],
    "FAILED_TO_CREATE_PROPERTY": ["Unable to create property. Please try again."],
    "FAILED_TO_LOAD_USER_OF_ORG": [
        "Unable to retrieve organization users. Please try again."
    ],
    "FAILED_TO_ANNOTATION_ROW": ["Unable to annotate row. Please try again."],
    "FAILED_TO_GET_DATASET_CONFIG": [
        "Unable to retrieve Hugging Face dataset configuration. Please try again."
    ],
    "FAILED_TO_CREATE_DATASET_FROM_HUGGINGFACE": [
        "Unable to create dataset from Hugging Face. Please try again."
    ],
    "FAILED_TO_LOAD_DATASET_FROM_HUGGINGFACE": [
        "Unable to load dataset from Hugging Face. Please try again."
    ],
    "FAILED_TO_CREATE_EMPTY_DATASET": [
        "Unable to create empty dataset. Please try again."
    ],
    "FAILED_TO_CREATE_DATASET_FROM_EXP": [
        "Unable to create dataset from experiment. Please try again."
    ],
    "FAILED_TO_IMPORT_ROWS_IN_EXISTING_DATASET": [
        "Unable to add rows to existing dataset. Please try again."
    ],
    "FAILED_TO_IMORT_ROWS_FROM_HUGGIGFACE_DATASET": [
        "Unable to import rows from Hugging Face dataset. Please try again."
    ],
    "FAILED_TO_ADD_ROWS_FROM_FILE": ["Unable to add rows from file. Please try again."],
    "FAILED_TO_ADD_SYNTHETIC_DATA": ["Unable to add synthetic data. Please try again."],
    "FAILED_TO_CREATE_SYNTHETIC_DATASET": [
        "Unable to create synthetic dataset. Please try again."
    ],
    "NOT_A_SYNTHETIC_DATASET": [
        "This dataset is not a synthetic dataset or does not have synthetic configuration."
    ],
    "FAILED_TO_GET_SYNTHETIC_DATASET_CONFIG": [
        "Unable to retrieve synthetic dataset configuration. Please try again."
    ],
    "FAILED_TO_UPDATE_SYNTHETIC_DATASET_CONFIG": [
        "Unable to update synthetic dataset configuration. Please try again."
    ],
    "FAILED_TO_CREATE_DATASET_FROM_LOCAL": [
        "Unable to create dataset from local files. Please try again."
    ],
    "FAILED_TO_CLONE_DATASET": ["Unable to clone dataset. Please try again."],
    "FAILED_TO_GET_DATASETS": ["Unable to retrieve datasets. Please try again."],
    "FAILED_TO_GET_DATASET_METADATA": [
        "Unable to retrieve dataset metadata. Please try again."
    ],
    "FAILED_TO_GET_EXP_DATASET_METADATA": [
        "Unable to retrieve experiment dataset metadata. Please try again."
    ],
    "FAILED_TO_GET_COLUMN": [
        "Unable to retrieve column configuration data. Please try again."
    ],
    "FAILED_TO_GET_DATASETS_NAMES": [
        "Unable to retrieve dataset names. Please try again."
    ],
    "FAILED_TO_ADD_COLUMN": ["Unable to add column. Please try again."],
    "FAILED_TO_ADD_EMPTY_COLUMN": ["Unable to add empty columns. Please try again."],
    "FAILED_TO_ADD_STATIC_COLUMN": ["Unable to add static columns. Please try again."],
    "FAILED_TO_DELETE_COLUMN": ["Unable to delete column. Please try again."],
    "FAILED_TO_DELETE_ROW": ["Unable to delete row. Please try again."],
    "FAILED_TO_ADD_EMPTY_ROWS": ["Unable to add empty rows. Please try again."],
    "FAILED_TO_ADD_SDK_ROWS": ["Unable to add SDK rows. Please try again."],
    "FAILED_TO_ADD_DATA_TO_ROWS": [
        "Unable to add dataset data to rows. Please try again."
    ],
    "FAILED_TO_DELETE_DATASET": ["Unable to delete dataset. Please try again."],
    "FAILED_TO_UPDATE_COLUMN_NAME": ["Unable to update column name. Please try again."],
    "FAILED_TO_EDIT_DATASET_BEHAVIOR": [
        "Unable to edit dataset behavior. Please try again."
    ],
    "FAILED_TO_UPDATE_CELL_VALUE": ["Unable to update cell value. Please try again."],
    "FAILED_TO_UPDATE_COLUMN_TYPE": ["Unable to update column type. Please try again."],
    "FAILED_TO_DOWNLOAD_DATASET": ["Unable to download dataset. Please try again."],
    "FAILED_TO_GET_EVAL_LISTS": [
        "Unable to retrieve evaluation templates. Please try again."
    ],
    "FAILED_TO_GET_EVAL_STRUCTURE": [
        "Unable to retrieve evaluation template structure. Please try again."
    ],
    "FAILED_TO_START_EVAL_PROCESS": [
        "Unable to start evaluation process. Please try again."
    ],
    "FAILED_TO_DELETE_EVALUATION": ["Unable to delete evaluation. Please try again."],
    "FAILED_TO_DELETE_EVAL_TEMP": [
        "Unable to delete evaluation template. Please try again."
    ],
    "FAILED_TO_UPDATE_EVALUATION_AND_PROCESS": [
        "Unable to update and run evaluation. Please try again."
    ],
    "FAILED_TO_PREVIEW_EVAL": ["Unable to preview evaluation. Please try again."],
    "FAILED_TO_GET_PROVIDER_STATUS": [
        "Unable to retrieve provider status. Please try again."
    ],
    "FAILED_TO_GET_DATASET_FROM_HUGGINGFACE": [
        "Unable to retrieve dataset from Hugging Face. Please try again."
    ],
    "FAILED_TO_FETCH_DATASET_DETAILS_FROM_HUGGINGFACE": [
        "Unable to fetch dataset details from Hugging Face. Please try again."
    ],
    "FAILED_TO_CREATE_JSON_COLUMN": ["Unable to create JSON column. Please try again."],
    "FAILED_TO_CREATE_CLASSIFY_COLUMN": [
        "Unable to create classification column. Please try again."
    ],
    "FAILED_TO_EXTRACT_ENTITY": [
        "Unable to extract entities from dataset. Please try again."
    ],
    "FAILED_TO_CREATE_API_COLUMN": ["Unable to create API column. Please try again."],
    "FAILED_TO_EXECUTE_CODE": ["Unable to execute Python code. Please try again."],
    "FAILED_TO_CRETE_CONDITIONAL_COLUMN": [
        "Unable to create conditional column. Please try again."
    ],
    "FAILED_TO_CREATE_VECTOR_DB_COLUMN": [
        "Unable to create vector database column. Please try again."
    ],
    "FAILED_TO_GET_EMBEDDINGS": [
        "Unable to retrieve embeddings list. Please try again."
    ],
    "FAILED_TO_CREATE_FEEDBACK": ["Unable to submit feedback. Please try again."],
    "FAILED_TO_GET_USER_EVAL_DATA": [
        "Unable to retrieve user evaluation metric data. Please try again."
    ],
    "FAILED_TO_GET_FEEDBACKS": ["Unable to retrieve feedback. Please try again."],
    "FAILED_TO_GET_FEEDBACK_SUMMARY": [
        "Unable to retrieve feedback summary. Please try again."
    ],
    "FAILED_TO_EVALUATE_ROW": ["Unable to evaluate row. Please try again."],
    "FAILED_TO_PREVIEW_DATASET": ["Unable to retrieve dataset info. Please try again."],
    "FAILED_TO_PREVIEW_DATASET_OPERATIONS": [
        "Unable to preview dataset operations. Please try again."
    ],
    "FAILED_TO_DUPLICATE_ROW": ["Unable to duplicate row. Please try again."],
    "FAILED_TO_DUPLICATE_DATASET": ["Unable to duplicate dataset. Please try again."],
    "FAILED_TO_MERGE_DATASETS": ["Unable to merge datasets. Please try again."],
    "FAILED_TO_GET_DERIVED_DATASET": [
        "Unable to retrieve derived dataset. Please try again."
    ],
    "FAILED_TO_GET_BASE_COLUMNS": [
        "Unable to retrieve base columns of dataset. Please try again."
    ],
    "FAILED_TO_COMPARE_DATASETS": ["Unable to compare datasets. Please try again."],
    "FAILED_TO_RE_RUN_EXP": ["Unable to re-run experiment. Please try again."],
    "FAILED_TO_CREATE_KB": ["Unable to create knowledge base. Please try again."],
    "FAILED_TO_LIST_KB": ["Unable to retrieve knowledge bases. Please try again."],
    "FAILED_TO_GET_KB": ["Unable to retrieve knowledge base. Please try again."],
    "FAILED_TO_UPDATE_KB": ["Unable to update knowledge base. Please try again."],
    "FAILED_TO_DELETE_KB": ["Unable to delete knowledge base. Please try again."],
    "FAILED_TO_GET_EMBEDDINGS_MODEL": [
        "Unable to retrieve supported embedding models. Please try again."
    ],
    "FAILED_TO_START_KB_OPTIMIZER": [
        "Unable to start knowledge base optimizer. Please try again."
    ],
    "FAILED_TO_PREVIEW_ANNOTATION": ["Unable to preview annotation. Please try again."],
    "FAILED_TO_GET_USER_EVAL_METRIC": [
        "Unable to retrieve user evaluation metric. Please try again."
    ],
    "FAILED_TO_CREATE_OPTIMIZE_DATASET": [
        "Unable to create optimized dataset. Please try again."
    ],
    "FAILED_TO_UPDATE_OPTIMIZE_DATASET": [
        "Unable to update optimized dataset. Please try again."
    ],
    "FAILED_TO_GET_METRICS_BY_COLUMN": [
        "Unable to retrieve metrics by column. Please try again."
    ],
    "FAILED_TO_CREATE_EVAL_TEMPLATE": [
        "Unable to create evaluation template. Please try again."
    ],
    "FAILED_TO_GET_OF_DATASET": [
        "Unable to retrieve evaluation columns of dataset. Please try again."
    ],
    "FAILED_TO_GET_EXP": ["Unable to retrieve experiment. Please try again."],
    "FAILED_TO_CREATE_EXP": ["Unable to create experiment. Please try again."],
    "FAILED_TO_UPDATE_EXP": ["Unable to update experiment. Please try again."],
    "FAILED_TO_GET_EXPs": ["Unable to retrieve experiments. Please try again."],
    "FAILED_TO_GET_EXP_DATA": ["Unable to retrieve experiment data. Please try again."],
    "FAILED_TO_COMPARE_EXPS": ["Unable to compare experiments. Please try again."],
    "FAILED_TO_ADD_ADDITIONAL_EVAL": [
        "Unable to add additional evaluations. Please try again."
    ],
    "FAILED_TO_ADD_EVALUATION_IN_EXP": [
        "Unable to add evaluation to experiment. Please try again."
    ],
    "FAILED_TO_GET_EXP_COMPARE_DATA": [
        "Unable to retrieve experiment comparison details. Please try again."
    ],
    "FAILED_TO_DELETE_EXP": ["Unable to delete experiment. Please try again."],
    "FAILED_TO_GET_COLUMN_VALUES": [
        "Unable to retrieve column values. Please try again."
    ],
    "FAILED_TO_FETCH_EXECUTION_DATA": [
        "Unable to retrieve prompt execution details. Please try again."
    ],
    "ERROR_CREATING_PROJECT_VERSION": [
        "Unable to create project version. Please try again."
    ],
    "ERROR_GETTING_PROJECT_VERSION_IDS": [
        "Unable to retrieve project version IDs. Please try again."
    ],
    "ERROR_DELETING_PROJECT_VERSION": [
        "Unable to delete project version. Please try again."
    ],
    "ERROR_FETCHING_PROJECT_VERSION": [
        "Unable to fetch project version. Please try again."
    ],
    "ERROR_UPDATING_PROJECT_VERSION_WINNNER": [
        "Unable to update project version winner. Please try again."
    ],
    "ERROR_UPDATING_PROJECT_VERSION_CONFIG": [
        "Unable to update project version configuration. Please try again."
    ],
    "FAILED_TO_GET_EXPORT_DATA": [
        "Unable to retrieve project version export data. Please try again."
    ],
    "ERROR_GETTING_RUN_INSIGHTS": ["Unable to fetch run insights. Please try again."],
    "ERROR_FETCHING_PROJECT_LISTS": [
        "Unable to retrieve project list. Please try again."
    ],
    "FAILED_TO_CREATE_PROJECT": ["Unable to create project. Please try again."],
    "FAILED_TO_UPDATE_PROJECT_CONFIG": [
        "Unable to update project configuration. Please try again."
    ],
    "ERROR_GETTING_TRACE": ["Unable to retrieve trace. Please try again."],
    "ERROR_COMPARING_TRACES": ["Unable to compare traces. Please try again."],
    "ERROR_CREATING_TRACES": ["Unable to create traces in bulk. Please try again."],
    "ERROR_GETTING_TRACE_LIST": ["Unable to retrieve traces list. Please try again."],
    "TOO_MANY_ROWS": [
        "Row limit reached. Please upgrade to a higher tier to avail more rows."
    ],
    "PROPERTY_NOT_FOUND": ["The specified property was not found."],
    "ANNOTATION_NOT_FOUND": ["No valid annotations found to delete."],
    "COLUMN_NOT_FOUND": ["Column not found with ID: "],
    "ROW_NOT_FOUND": ["The specified row was not found."],
    "EMPTY_DATASET": ["The dataset is empty."],
    "COLUMN_OR_CELL_NOT_FOUND": ["The specified column or cell does not exist."],
    "USER_NOT_FOUND_IN_ORG": ["No users found in the specified organization."],
    "EVAL_TEMP_NOT_FOUND": ["The specified evaluation template was not found."],
    "INSIGHT_NOT_FOUND": ["The specified insight was not found."],
    "AI_MODEL_NOT_FOUND": ["The specified AI model was not found."],
    "PERFORMANCE_REPORT_NOT_FOUND": ["The specified performance report was not found."],
    "PROVIDER_CONFIG_NOT_FOUND": ["No default provider has been configured."],
    "PROVIDER_NOT_FOUND": ["The specified provider was not found."],
    "DATASETS_NOT_FOUND": ["No datasets were found."],
    "ROW_COUNT_EXCEEDS_LIMIT": [
        "Number of rows exceeds the maximum allowed limit of {}."
    ],
    "COLUMN_COUNT_EXCEEDS_LIMIT": [
        "Number of columns exceeds the maximum allowed limit of {}."
    ],
    "FILE_SIZE_EXCEEDS_LIMIT": [
        "File size exceeds the maximum allowed limit of {} MB."
    ],
    "BATCH_SIZE_EXCEEDS_LIMIT": [
        "Batch size exceeds the maximum allowed limit of {} items."
    ],
    "UPDATE_REQUIRES_AT_LEAST_ONE_FIELD": [
        "At least one field must be provided for update."
    ],
    "US25": ["Internal Server Error"],
    "DATASET_NAME_EXISTS": [
        "Dataset name already exists. Please use a different name."
    ],
    "INVALID_COLUMN_TYPE_CHANGE": [
        "Editing column type is not allowed for this column."
    ],
    "RATE_LIMIT_REACHED": [
        "Rate limit reached. Please try again later or upgrade your plan."
    ],
    "INSUFFICIENT_CREDITS": ["Insufficient credits. Please recharge your account."],
    "INSUFFICIENT_VOICE_CALL_BALANCE": [
        "Insufficient balance for voice call. Please recharge your account to make calls."
    ],
    "INSUFFICIENT_CHAT_CALL_BALANCE": [
        "Insufficient balance for chat call. Please recharge your account to execute chats."
    ],
    "FAILED_TO_COMPARE_DATASETS_STATS": [
        "Unable to compare dataset statistics. Please try again."
    ],
    "COLUMN_NAME_EXISTS": ["Column name must be unique in dataset"],
    "COLUMN_NAME_TOO_LONG": ["Column name must not exceed 255 characters."],
    "DUPLICATE_COLUMN_NAMES_IN_REQUEST": ["Duplicate column names found in request."],
    "EVAL_NAME_EXISTS": ["Eval Template name must be unique"],
    "EXPERIMENT_NAME_EXISTS": ["Experiment name must be unique"],
    "DUPLICATE_COLUMN_NAME": ["Column names must be unique in the dataset."],
    "OPTIMIZATION_NAME_EXISTS": ["Optimization name must be unique"],
    "NO_DATASET_INFO_PROVIDED": ["No dataset info provided."],
    "NO_COMMON_COLUMNS_PROVIDED": ["No common columns provided."],
    "NO_DATASET_IDS_PROVIDED": ["No dataset IDs provided."],
    "INVALID_DATASET_IDS": ["Invalid dataset IDs provided."],
    "INVALID_DATETIME_FORMAT": ["Invalid datetime format."],
    "FAILED_TO_UPDATE_ROW": ["Unable to update row. Please try again."],
    "FAILED_TO_GET_KNOWLEDGE_BASE": [
        "Unable to retrieve knowledge base. Please try again."
    ],
    "FAILED_TO_CREATE_KNOWLEDGE_BASE": [
        "Unable to create knowledge base. Please try again."
    ],
    "FAILED_TO_UPDATE_KNOWLEDGE_BASE": [
        "Unable to update knowledge base. Please try again."
    ],
    "MISSING_KNOWLEDGE_BASE_ID_OR_ORGANIZATION": [
        "Knowledge base ID and organization ID are required."
    ],
    "FAILED_TO_DELETE_KNOWLEDGE_BASE_FILES": [
        "Unable to delete knowledge base files. Please try again."
    ],
    "INVALID_FILES_PROVIDED": ["Invalid files provided. Please try again."],
    "KNOWLEDGE_BASE_NOT_FOUND": ["Knowledge base not found."],
    "KNOWLEDGE_BASE_ALREADY_EXISTS": ["Knowledge base name must be unique."],
    "NO_EVALUATION_COLUMNS_FOUND": ["No evaluation columns present in given datasets."],
    "MAX_KB_SIZE_EXCEEDED": ["Maximum knowledge base size exceeded."],
    "KB_CREATION_LIMIT_REACHED": [
        "Knowledge base creation limit reached. Please upgrade your plan."
    ],
    "FAILED_TO_CREATE_DATASET_FROM_OBSERVATION_SPAN": [
        "Unable to create dataset from observation span."
    ],
    "COLUMN_ALREADY_EXISTS": ["Column with this name already exists in the dataset."],
    "DUPLICATE_FILES": ["Duplicate files found. Please remove duplicates."],
    "FILE_ALREADY_EXISTS": ["File with same name already exists."],
    "MISSING_FILE_IDS_OR_NAMES": ["File IDs or names are required."],
    "AUDIO_FORMAT_NOT_RECOGNIZED": ["Huggingface Audio Format not recognised"],
    "UNSUPPORTED_IMAGE_FORMAT": ["The provided image format is not supported."],
    "INVALID_BASE64_FILE": ["The provided base64 file string is invalid or corrupted."],
    "INVALID_IMAGE": ["The provided image is invalid or corrupted."],
    "EMPTY_DATA": ["The provided data is empty."],
    "FAILED_TO_GET_DATA_FROM_HUGGINGFACE": [
        "Unable to get data from Hugging Face. Please try again later."
    ],
    "INVALID_ROW_ID": ["The provided row ID is invalid."],
    "UNSUPPORTED_AUDIO_FORMAT": ["The provided audio format is not supported."],
    "API_ALERT_TITLE": ["{} {} limit reached."],
    "API_ALERT_TEMPLATE": [
        "You have reached the {} {} limit. Please Try again in 1 {}."
    ],
    "API_SUBS_TITLE": ["Want to process more data per {}?"],
    "API_SUBS_TEMPLATE": [
        "Upgrade your plan or contact sales to increase your API limits.",
    ],
    "RESOURCE_ALERT_TITLE": ["{} limit reached."],
    "RESOURCE_ALERT_TEMPLATE": [
        "Error occured while uploading. Your current plan supports only {} {}. To add more, please upgrade your plan. "
    ],
    "RESOURCE_SUBS_TITLE": ["Want to add more {}?"],
    "RESOURCE_SUBS_TEMPLATE": ["Upgrade your plan to add more {}."],
    "MEMBER_LIMIT_REACHED": [
        "{} Subscription Only Allows {} member(s) in Organization. Wallet balance is insufficient to add more users"
    ],
    "INVALID_API_CALL_TYPE": ["Invalid API call type."],
    "OBSERVATION_SPAN_NOT_FOUND": ["Observation span not found for this organization."],
    "CUSTOM_EVAL_CONFIG_NOT_FOUND": [
        "Custom evaluation config not found for this organization."
    ],
    "UNABLE_TO_FETCH_CHECKS": ["Unable to fetch data. Please try again later."],
    "MISSING_COLUMNS_FOR_DATASETS": ["Missing columns in datasets."],
    "FAILED_TO_DELETE_PROJECT": ["Unable to delete project. Please try again."],
    "FAILED_TO_UPDATE_PROJECT_NAME": [
        "Unable to update project name. Please try again."
    ],
    "PROJECT_NOT_FOUND": ["Project Not Found"],
    "FAILED_SPAN_EXPORT": ["Error exporting the spans list of observe"],
    "FAILED_TRACE_EXPORT": ["Error exporting the traces list of observe"],
    "FAILED_TRACE_SESSION_EXPORT": [
        "Error exporting the trace session list of observe"
    ],
    "TEMPLATE_CREATION_FAILED": ["Unable to create template. Please try again."],
    "VERSION_CREATION_FAILED": ["Unable to create version. Please try again."],
    "VERSION_NOT_EXIST": ["Version not found."],
    "MISSING_COLUMN_MAPPING": ["Column mapping is required."],
    "FAILED_TO_CREATE_TEMPLATE": ["Unable to save evaluation template."],
    "SINGLE_VERSION_REQUIRED": ["Please select only one version to view results."],
    "VERSIONS_REQUIRED": ["At least one version is required."],
    "TEMPLATE_NOT_EXISTS": ["Template not found."],
    "UNABLE_TO_GET_VARIABLES": ["Unable to get variables."],
    "UNABLE_TO_RUN_TEMPLATE": ["Unable to run prompt."],
    "UNABLE_TO_GET_RUN_STATUS": ["Unable to get run status."],
    "UNABLE_TO_COMMIT": ["Unable to commit."],
    "UNABLE_TO_FETCH_TEMPLATE_HISTORY": ["Unable to fetch template history."],
    "INVALID_VERSION_PROVIDED": ["Invalid Version Provided."],
    "MAX_3_VERSIONS_ALLOWED": ["Maximum of 3 versions are allowed"],
    "FAILED_TO_UPDATE_ALERT": ["Unable to update alert. Please try again."],
    "ALERT_NOT_FOUND": ["Alert Monitor not found for the provided ID."],
    "FAILED_TO_GET_MONITOR": [
        "Unable to retrieve details for the selected alert. Please try again."
    ],
    "NO_CHANGES_MADE": ["No changes were made to the alert."],
    "PROJECT_ID_REQUIRED": ["Project ID is required."],
    "UNABLE_TO_GET_NEXT_VERSION": ["Unable to get next version."],
    "UNABLE_TO_ADD_API_KEY": ["Unable to add api key."],
    "MODEL_VALIDATION_FAILED": [
        "Failed to validate model. Please enter correct details."
    ],
    "MODEL_PROVIDER_NOT_FOUND": ["The specified model provider was not found."],
    "MISSING_MODEL_PROVIDER": ["Please specify the model provider."],
    "MISSING_JSON_KEY": [
        "Please provide the JSON Configuration file or the API key for the selected provider."
    ],
    "MISSING_AZURE_KEY": [
        "Please provide API base url, API key (and API version for legacy endpoints) for the selected provider."
    ],
    "MISSING_AWS_KEY": [
        "Please provide Access Key, Secret Access Key and Region Name or the API key for the selected provider."
    ],
    "MISSING_OPENAI_KEY": ["OpenAI Key not provided"],
    "MISSING_MODEL_ID": ["Model ID is required."],
    "UNABLE_TO_CREATE_MODEL": ["Unable to create model. Please try again."],
    "EVAL_STACK_UPDATED": [
        "Eval stack upgraded - this version unsupported. Use custom evals instead."
    ],
    "CONTAINS_ARBITRARY_CODE": [
        "This dataset is not supported as it contains arbitrary code."
    ],
    "FAILED_TO_GET_ROW_DIFF": ["Unable to retrieve row differences. Please try again."],
    "INVALID_REQUEST_DATA": ["The request data is missing required keys."],
    "KEY_ID_REQUIRED": ["Key ID is required."],
    "FAILED_TO_DELETE_KEY": ["Unable to delete requested keys. Please try again."],
    "KEY_DOES_NOT_EXIST": ["No keys are associated with provided Key ID."],
    "UNABLE_TO_GENERATE_KEY": ["Unable to generate keys. Please try again."],
    "SECRET_KEY_NOT_DISABLED": ["Keys not disabled. Please try again."],
    "API_KEY_DISABLED": ["Keys already disabled."],
    "API_KEY_ENABLED": ["Keys already enabled."],
    "SECRET_KEY_NOT_ENABLED": ["Keys not enabled. Please try again."],
    "FAILED_TO_GET_KEYS": ["Unable to fetch keys. Please try again."],
    "KEY_NAME_EXISTS": ["Keys with this already name exists."],
    "MODEL_NAME_ALREADY_EXISTS": [
        "Model name already exists. Please use a different name."
    ],
    "INVALID_FORMAT": ["Keys and values must be strings."],
    "LOG_ID_REQUIRED": ["Please select one or more logs to delete."],
    "ERROR_DELETING_LOG": ["Unable to delete selected log entries. Please try again."],
    "COLUMN_CONFIG_NOT_UPDATED": [
        "Unable to update the column config. Please try again."
    ],
    "LOG_ROW_FETCHING_FAILED": ["Unable to fetch log row details. Please try again."],
    "MISSING_TEMPLATE_TYPE": ["No template type provided"],
    "EVAL_IDS_REQUIRED": ["Evaluation IDs are required."],
    "PROMPT_EVAL_CONFIG_IDS_REQUIRED": [
        "Prompt evaluation configuration IDs are required."
    ],
    "EVALUATION_NOT_FOR_ERROR_CELL": ["Evaluation not possible on error cell."],
    "MEMBER_ALREADY_ACTIVE": ["Cannot Update Role of Active User."],
    "ROLE_NOT_MENTIONED": ["Please mention the updated role of the user."],
    "USER_CANNOT_CHANGE_ROLE": ["You cannot change your own role."],
    "MEMBER_NOT_IN_ORG": ["User not found in the organization."],
    "UNABLE_TO_UPDATE_ROLE": ["Unable to update role. Please try again."],
    "FAILED_TO_RUN_EVAL": ["Unable to run evaluation. Please try again."],
    "ERROR_AUDIO_UPLOAD": ["Audio upload failed. Try again."],
    "FAILED_TO_RERUN_OPERATION": ["Unable to rerun operation. Please try again."],
    "MONITOR_NOT_FOUND": ["Alert Monitor not found for the provided ID."],
    "MISSING_OPERATION_TYPE": ["Operation type is required."],
    "INVALID_PYTHON_CODE_CONFIGURATION": ["Invalid Python code configuration."],
    "INVALID_JSON_EXTRACTION_CONFIGURATION": ["Invalid JSON extraction configuration."],
    "INVALID_API_CALL_CONFIGURATION": ["Invalid API call configuration."],
    "INVALID_CLASSIFICATION_CONFIGURATION": ["Invalid classification configuration."],
    "INVALID_ENTITY_EXTRACTION_CONFIGURATION": [
        "Invalid entity extraction configuration."
    ],
    "FAILED_TO_GET_OPERATION_CONFIGURATIONS": [
        "Unable to get operation configurations. Please try again."
    ],
    "FAILED_TO_GET_MONITOR_DETAILS": [
        "Unable to retrieve details for the selected alert. Please try again."
    ],
    "EVAL_TEMPLATE_ALREADY_EXISTS": [
        "Evaluation template with this name already exists."
    ],
    "EVALUATION_NOT_FOUND": ["Evaluation not found."],
    "FAILED_TO_GET_EVAL_RESULTS": [
        "Unable to retrieve evaluation results. Please try again."
    ],
    "USER_ALREADY_EXISTS": ["User already exists in the other organization."],
    "FAILED_TO_GET_EVALUATION_RUN_SUMMARY": [
        "Unable to retrieve evaluation run summary. Please try again."
    ],
    "UNAUTHORIZED_ACCESS": ["You are not authorized to access this resource."],
    "UNABLE_TO_FETCH_PRICING_DATA": ["Unable to fetch pricing data. Please try again."],
    "UNABLE_TO_FETCH_ANNOTATION_SUMMARY": [
        "Unable to fetch annotation summary. Please try again"
    ],
    "UNABLE_TO_FETCH_EVAL_SUMMARY": [
        "Unable to fetch evaluation summary. Please try again"
    ],
    "UNABLE_TO_FETCH_EVAL_REASON_SUMMARY": [
        "Unable to fetch evaluation reason summary. Please try again"
    ],
    "UNABLE_TO_REFRESH_EVAL_REASON_SUMMARY": [
        "Unable to refresh evaluation reason summary. Please try again"
    ],
    "TEST_EXECUTION_NOT_FOUND": ["Test execution not found."],
    "PROMPT_EVAL_TEMPLATE_EXISTS": [
        "Prompt evaluation template with this name already exists."
    ],
    "ERROR_FETCHING_OBSERVABILITY_PROVIDERS": [
        "Unable to retrieve observability providers. Please try again."
    ],
    "FAILED_TO_CREATE_OBSERVABILITY_PROVIDER": [
        "Unable to create observability provider. Please try again."
    ],
    "OBSERVABILITY_PROVIDER_NOT_FOUND": ["Observability provider not found."],
    "FAILED_TO_UPDATE_OBSERVABILITY_PROVIDER": [
        "Unable to update observability provider. Please try again."
    ],
    "FAILED_TO_DELETE_OBSERVABILITY_PROVIDER": [
        "Unable to delete observability provider. Please try again."
    ],
    "FAILED_TO_CREATE_TTS_VOICE": [
        "Unable to add TTS voice. Please check the voice ID."
    ],
    "REPLAY_TYPE_REQUIRED": ["Replay type is required. Must be 'session' or 'trace'."],
    "INVALID_REPLAY_TYPE": ["Invalid replay type. Must be 'session' or 'trace'."],
    "REPLAY_IDS_OR_SELECT_ALL_REQUIRED": ["Either ids or select_all is required."],
    "AGENT_NAME_REQUIRED": ["Agent name is required."],
    "SCENARIO_NAME_REQUIRED": ["Scenario name is required."],
    "DUPLICATE_CUSTOM_COLUMN_NAMES": ["Duplicate column name(s) in custom columns: {}"],
    "FAILED_TO_CREATE_AGENT_AND_SCENARIOS": [
        "Failed to create agent and scenarios. Please try again."
    ],
    "FAILED_TO_PREFETCH_AGENT_DATA": [
        "Failed to prefetch agent data. Please try again."
    ],
    "FAILED_TO_GET_AGENT_DEFINITION": [
        "Failed to get agent definition. Please try again."
    ],
    "FAILED_TO_GET_EVAL_CONFIGS": ["Failed to get eval configs. Please try again."],
    "REPLAY_SESSION_NOT_FOUND": ["Replay session not found."],
    "FAILED_TO_LIST_REPLAY_SESSIONS": [
        "Failed to list replay sessions. Please try again."
    ],
    "FAILED_TO_GET_REPLAY_SESSION": ["Failed to get replay session. Please try again."],
    "FAILED_TO_CREATE_REPLAY_SESSION": [
        "Failed to create replay session. Please try again."
    ],
    "FAILED_TO_GENERATE_SCENARIO": ["Failed to generate scenario. Please try again."],
    ## LiteLLM error codes mapping
    "LITELLM_BAD_REQUEST": [
        "There seems to be an issue with your input. Please check the documentation from the provider or contact Future AGI support"
    ],
    "LITELLM_UNSUPPORTED_PARAMS": [
        "Unsupported params passed. Please check the params and try again."
    ],
    "LITELLM_CONTEXT_WINDOW_EXCEEDED": [
        "Context window exceeded. Please check the context window and try again."
    ],
    "LITELLM_CONTENT_POLICY_VIOLATION": [
        "Content policy violation. Please check the content and try again."
    ],
    "LITELLM_IMAGE_FETCH_ERROR": [
        "An error occured while fetching or processing images. Please check the image and try again."
    ],
    "LITELLM_INVALID_REQUEST": [
        "Invalid request. Please check the request and try again."
    ],
    "LITELLM_AUTHENTICATION_ERROR": [
        "Authentication error. Please check your respective provider API key"
    ],
    "LITELLM_PERMISSION_DENIED": [
        "Permission denied. Please check your respective provider permissions"
    ],
    "LITELLM_NOT_FOUND": [
        "Provider not found or invalid model passed. Please check the model and try again."
    ],
    "LITELLM_TIMEOUT": ["Request timed out"],
    "LITELLM_UNPROCESSABLE_ENTITY": [
        "Unprocessable entity. Please check the entity and try again."
    ],
    "LITELLM_RATE_LIMIT": [
        "Rate limit exceeded. Please upgrade your respective provider plan"
    ],
    "LITELLM_API_CONNECTION_ERROR": [
        "API connection error. Please check with your respective provider for any API issues."
    ],
    "LITELLM_API_ERROR": ["API error. Please check the API and try again."],
    "LITELLM_INTERNAL_SERVER_ERROR": [
        "An internal server error occurred from the provider."
    ],
    "LITELLM_SERVICE_UNAVAILABLE": [
        "Provider returned a service unavailable error. Please try again later."
    ],
    "LITELLM_API_RESPONSE_VALIDATION_ERROR": [
        "API response validation error. Please check the API response and try again."
    ],
    "LITELLM_BUDGET_EXCEEDED": [
        "Budget exceeded. Please upgrade your respective provider plan"
    ],
    "LITELLM_JSON_SCHEMA_VALIDATION_ERROR": [
        "Response does not match expected json schema. Please check the JSON schema or try again."
    ],
    "FAILED_TO_FETCH_DATA": ["Failed to fetch data. Please try again later."],
    "PROMPT_TRIAL_NOT_FOUND": ["The specified prompt trial was not found."],
    "FAILED_TO_OPTIMISE_PROMPT": [
        "Failed to Optimise Prompt. Please contact FutureAGI support."
    ],
    "SESSION_COMPARISON_FAILED_CHAT_SIM": [
        "Failed to compare session chat simulations. Please try again."
    ],
    "GRAPH_NOT_FOUND": ["Graph not found."],
    "FAILED_TO_LIST_GRAPHS": ["Failed to list graphs."],
    "FAILED_TO_CREATE_GRAPH": ["Failed to create graph."],
    "FAILED_TO_RETRIEVE_GRAPH": ["Failed to retrieve graph."],
    "FAILED_TO_UPDATE_GRAPH": ["Failed to update graph."],
    "FAILED_TO_DELETE_GRAPH": ["Failed to delete graph."],
    "FAILED_TO_LIST_VERSIONS": ["Failed to list versions."],
    "FAILED_TO_CREATE_VERSION": ["Failed to create version."],
    "VERSION_NOT_FOUND": ["Version not found."],
    "FAILED_TO_RETRIEVE_VERSION": ["Failed to retrieve version."],
    "ONLY_DRAFT_VERSIONS_UPDATABLE": ["Can only update draft versions."],
    "FAILED_TO_UPDATE_VERSION": ["Failed to update version."],
    "CANNOT_DELETE_ONLY_VERSION": ["Cannot delete the only version."],
    "FAILED_TO_DELETE_VERSION": ["Failed to delete version."],
    "ONLY_INACTIVE_VERSIONS_ACTIVATABLE": ["Only inactive versions can be activated."],
    "FAILED_TO_ACTIVATE_VERSION": ["Failed to activate version."],
    "FAILED_TO_GET_REFERENCEABLE_GRAPHS": ["Failed to get referenceable graphs."],
    "GRAPHS_REFERENCED_BY_OTHER_GRAPHS": [
        "Cannot delete: some graphs are referenced by other graphs that are not being deleted."
    ],
    "NODE_TEMPLATE_NOT_FOUND": ["Node template not found."],
    "FAILED_TO_LIST_NODE_TEMPLATES": ["Failed to list node templates."],
    "FAILED_TO_RETRIEVE_NODE_TEMPLATE": ["Failed to retrieve node template."],
    "GRAPH_DATASET_NOT_FOUND": ["Graph dataset not found."],
    "FAILED_TO_GET_GRAPH_DATASET": ["Failed to retrieve graph dataset."],
    "NO_COLUMNS_EXIST": ["No columns exist. Activate a graph version first."],
    "FAILED_TO_CREATE_DATASET_ROW": ["Failed to create dataset row."],
    "CANNOT_DELETE_ALL_ROWS": ["Cannot delete all rows. At least one row must remain."],
    "FAILED_TO_DELETE_DATASET_ROWS": ["Failed to delete dataset rows."],
    "DATASET_ROWS_NOT_FOUND": ["Some rows were not found."],
    "CELL_NOT_FOUND": ["Cell not found."],
    "FAILED_TO_UPDATE_DATASET_CELL": ["Failed to update cell value."],
    "GRAPH_VERSION_NOT_FOUND_FOR_GRAPH": ["Graph version not found for this graph."],
    "GRAPH_VERSION_NOT_ACTIVE": ["Graph version must be active to execute."],
    "FAILED_TO_EXECUTE_DATASET_ROWS": ["Failed to execute dataset rows."],
    "FAILED_TO_LIST_EXECUTIONS": ["Failed to list executions."],
    "GRAPH_EXECUTION_NOT_FOUND": ["Graph execution not found."],
    "FAILED_TO_GET_EXECUTION_DETAIL": ["Failed to retrieve execution detail."],
    "NODE_EXECUTION_NOT_FOUND": ["Node execution not found."],
    "FAILED_TO_GET_NODE_EXECUTION_DETAIL": [
        "Failed to retrieve node execution detail."
    ],
    "NODE_NOT_FOUND": ["Node not found."],
    "PORT_NOT_FOUND": ["Port not found."],
    "NODE_CONNECTION_NOT_FOUND": ["Node connection not found."],
    "FAILED_TO_CREATE_NODE": ["Failed to create node."],
    "FAILED_TO_RETRIEVE_NODE": ["Failed to retrieve node."],
    "FAILED_TO_UPDATE_NODE": ["Failed to update node."],
    "FAILED_TO_DELETE_NODE": ["Failed to delete node."],
    "FAILED_TO_UPDATE_PORT": ["Failed to update port."],
    "FAILED_TO_CREATE_NODE_CONNECTION": ["Failed to create node connection."],
    "FAILED_TO_DELETE_NODE_CONNECTION": ["Failed to delete node connection."],
    "FAILED_TO_GET_EDGE_MAPPINGS": ["Failed to retrieve possible edge mappings"],
    # RBAC
    "USER_NOT_IN_ORG": ["User is not part of any organization."],
    "NOT_ORG_MEMBER": ["You are not a member of this organization."],
    "INVITE_LEVEL_FORBIDDEN": ["You cannot invite users above your own role level."],
    "INVITE_WS_ACCESS_FORBIDDEN": [
        "You do not have workspace admin access to one or more specified workspaces."
    ],
    "INVITE_RESEND_WS_FORBIDDEN": [
        "You can only resend invites for workspaces you manage."
    ],
    "INVITE_LEVEL_SET_FORBIDDEN": [
        "You cannot set an invite level at or above your own level."
    ],
    "INVITE_CANCEL_WS_FORBIDDEN": [
        "You can only cancel invites for workspaces you manage."
    ],
    "INVITE_NOT_FOUND": ["Invite not found."],
    "WORKSPACE_NOT_FOUND": ["Workspace not found."],
    "WS_NOT_IN_ORG": ["Workspace not found in organization."],
    "MEMBER_NOT_IN_ORG": ["User is not a member of this organization."],
    "MEMBER_DEACTIVATED_ROLE_UPDATE": [
        "Cannot update role for a deactivated member. Reactivate them first."
    ],
    "ROLE_ASSIGN_FORBIDDEN": ["You cannot assign a role at or above your own level."],
    "LAST_OWNER_DEMOTE": ["Cannot demote the last owner of the organization."],
    "LAST_OWNER_REMOVE": ["Cannot remove the last owner of the organization."],
    "CANNOT_INVITE_SELF": ["You cannot invite yourself."],
    "ALREADY_ORG_MEMBER": ["is already an active member of this organization."],
    "CANNOT_REMOVE_SELF": ["You cannot remove yourself."],
    "CANNOT_REACTIVATE_SELF": ["You cannot reactivate yourself."],
    "REACTIVATE_LEVEL_FORBIDDEN": [
        "You cannot reactivate a user at or above your own level."
    ],
    "NO_DEACTIVATED_MEMBERSHIP": ["No deactivated membership found."],
    "WS_ADMIN_REQUIRED": ["Workspace admin or organization admin access required."],
    "WS_ROLE_MODIFY_ORG_ADMIN": [
        "Cannot change workspace role for organization admins or owners. "
        "They automatically have workspace admin access."
    ],
    "WS_MEMBER_NOT_FOUND": ["User is not a member of this workspace."],
    "WS_ROLE_MODIFY_FORBIDDEN": [
        "You cannot modify the role of a member at or above your workspace level."
    ],
    "WS_ROLE_ASSIGN_FORBIDDEN": [
        "You cannot assign a workspace role at or above your own level."
    ],
    "CANNOT_REMOVE_SELF_FROM_WS": ["You cannot remove yourself from the workspace."],
    "CANNOT_REMOVE_ORG_ADMIN_FROM_WS": [
        "Cannot remove organization admins or owners from workspaces. "
        "They have automatic access to all workspaces."
    ],
    "CANNOT_REMOVE_LAST_WS": [
        "Cannot remove member from their only workspace. "
        "To remove them entirely, use 'Remove from organization' instead."
    ],
    "ANALYTICS_RUN_TEST_NOT_FOUND": ["Run test not found."],
    "ANALYTICS_EXECUTION_NOT_FOUND": ["Test execution not found."],
    "ANALYTICS_CALL_EXECUTION_NOT_FOUND": ["Call execution not found."],
    "ANALYTICS_METRICS_ERROR": ["Unable to fetch simulation metrics."],
    "ANALYTICS_RUNS_ERROR": ["Unable to fetch simulation runs."],
    "ANALYTICS_ERROR": ["Unable to fetch simulation analytics."],
}


def get_error_message(code, index=0):
    try:
        return f"{err_dict.get(code)[index]}"
    except TypeError:
        return "An unknown error occurred."


def get_specific_error_message(error, is_llm_error=None):
    """
    Maps specific error types, statuses, or messages to the appropriate error code.
    Returns the appropriate error message from err_dict.

    Parameters:
    - error: The error object, status string, or error message to analyze

    Returns:
    - A user-friendly error message string
    """
    error_message = str(error)
    error_message_lower = error_message.lower()
    error_status = None

    # Check if its litellm Error
    if is_llm_error:
        return error_message

    # Check if it's a ValueError with a tuple structure
    if (
        isinstance(error, ValueError)
        and isinstance(error.args, tuple)
        and len(error.args) > 1
    ):
        error_status = str(error.args[1])

    # Check for rate limit errors.
    # NOTE: Avoid matching generic "rate" tokens from normal validation errors.
    if error_status and (
        "rate_limited" in error_status
        or "rate limit" in error_status.lower()
        or "too many requests" in error_status.lower()
        or "429" in error_status
    ):
        return get_error_message("RATE_LIMIT_REACHED")

    # For plain ValueError messages, prefer returning user-facing validation text
    # rather than coercing to RATE_LIMIT_REACHED via broad keyword matching.
    if isinstance(error, ValueError) and not error_status:
        pass
    elif (
        "rate_limited" in error_message_lower
        or "rate limit" in error_message_lower
        or "too many requests" in error_message_lower
        or "429" in error_message
    ):
        return get_error_message("RATE_LIMIT_REACHED")

    # Check for insufficient credits errors
    if error_status and (
        "insufficient_credits" in error_status or "Credits" in error_status
    ):
        return get_error_message("INSUFFICIENT_CREDITS")
    if (
        "insufficient_credits" in error_message
        or "insufficient" in error_message.lower()
        or "Insufficient Credits" in error_message
    ):
        return get_error_message("INSUFFICIENT_CREDITS")

    # Check for specific known error patterns
    if error_message in [
        "Invalid context format",
        "Unable to generate",
        "All values in",
        "Invalid format",
    ]:
        return error_message

    # If it's a ValueError with a tuple structure but not one of the specific cases above
    if (
        isinstance(error, ValueError)
        and isinstance(error.args, tuple)
        and len(error.args) > 1
    ):
        return f"{error.args[0]} {error.args[1]}"

    # If it contains "API call not allowed" text
    if "API call not allowed" in error_message:
        return error_message

    # Default case - return the original error message if it's a ValueError
    if isinstance(error, ValueError):
        if error_message in err_dict:
            return get_error_message(error_message)
        return error_message

    # For any other exception, use the generic error message
    return get_error_message("FAILED_TO_PROCESS_EVALUATION")


def get_error_for_api_status(status):
    """
    Maps API call status codes to appropriate error messages.
    This function can be used to directly get an error message for a status code.

    Parameters:
    - status: The API call status code

    Returns:
    - A user-friendly error message string
    """
    status_str = str(status).lower()

    # Map common API statuses to error codes
    if "rate_limited" in status_str:
        return get_error_message("RATE_LIMIT_REACHED")
    elif "insufficient_credits" in status_str:
        return get_error_message("INSUFFICIENT_CREDITS")
    else:
        return f"API call not allowed: {status}"


# ── Billing & Entitlement Error Codes (Phase 4.7) ──────────────────────────
# These codes are returned by billing/entitlement checks in API responses.
# Each entry: code → [message]. HTTP status is set by the view, not here.
#
# Code                    | HTTP | Trigger
# FREE_TIER_LIMIT         | 403  | Free plan allowance exhausted
# RATE_LIMITED             | 429  | API rate limit exceeded
# ENTITLEMENT_LIMIT       | 403  | Resource count limit reached (monitors, KBs, etc.)
# ENTITLEMENT_DENIED      | 403  | Boolean feature not on current plan
# BUDGET_PAUSED           | 403  | User's own budget paused this dimension
# PAYMENT_REQUIRED        | 402  | No payment method on file
# ACCOUNT_SUSPENDED       | 403  | Payment failed, account in dunning
# LICENSE_EXPIRED         | 403  | EE license expired
# LICENSE_FEATURE_DENIED  | 403  | EE license lacks this feature
BILLING_ERROR_CODES = {
    "FREE_TIER_LIMIT": [
        "Free tier allowance exhausted. Upgrade to Pay-as-you-go to continue."
    ],
    "RATE_LIMITED": ["Too many requests. Please wait and retry."],
    "ENTITLEMENT_LIMIT": [
        "Resource limit reached for your current plan. Upgrade to increase limits."
    ],
    "ENTITLEMENT_DENIED": ["This feature is not available on your current plan."],
    "BUDGET_PAUSED": [
        "Usage paused by your budget rule. Raise or remove the budget to continue."
    ],
    "PAYMENT_REQUIRED": [
        "A payment method is required. Please add a card to continue."
    ],
    "ACCOUNT_SUSPENDED": [
        "Your account has an outstanding payment issue. Please update your payment method."
    ],
    "LICENSE_EXPIRED": [
        "Your Enterprise Edition license has expired. Please renew to continue using EE features."
    ],
    "LICENSE_FEATURE_DENIED": [
        "This feature is not included in your current license band. Upgrade to access it."
    ],
}

# Merge billing error codes into the main dict
err_dict.update(BILLING_ERROR_CODES)

# ── Login Error Codes ────────────────────────────────────────────────────
LOGIN_ERROR_CODES = {
    "LOGIN_IP_BLOCKED": [
        "Your IP address has been temporarily blocked due to multiple failed attempts. Please try again later."
    ],
    "LOGIN_IP_RATE_LIMITED": [
        "Too many login attempts from your network. Please try again later."
    ],
    "LOGIN_ACCOUNT_BLOCKED": [
        "Account temporarily blocked due to too many failed attempts. Please try again later."
    ],
    "LOGIN_RECAPTCHA_FAILED": ["reCAPTCHA verification failed. Please try again."],
    "LOGIN_INVALID_CREDENTIALS": [
        "Invalid email or password. Please check your credentials and try again."
    ],
    "LOGIN_ACCOUNT_DEACTIVATED": [
        "Your account has been deactivated. Please contact your organization admin."
    ],
    "LOGIN_TOO_MANY_ATTEMPTS": [
        "Too many failed login attempts. Your account has been temporarily blocked."
    ],
    "LOGIN_UNEXPECTED_ERROR": [
        "An unexpected error occurred during login. Please try again."
    ],
    "LOGIN_PASSWORD_RESET_RATE_LIMITED": [
        "Too many password reset requests. Please try again later."
    ],
}

err_dict.update(LOGIN_ERROR_CODES)

# get_error_message("METRIC_VALID_CONTENT")
