package eligibility

// Rejection codes: an auto rejection's reason, the decision record's
// rejected[].reason, and metadata.reason of a refused explicit model.
const (
	CodeKeyModelDenied          = "key_model_denied"
	CodeKeyProviderDenied       = "key_provider_denied"
	CodeOrgProviderDenied       = "org_provider_denied"
	CodeManagedNotEnabled       = "managed_not_enabled"
	CodeManagedModelNotAllowed  = "managed_model_not_allowed"
	CodeModelBlocked            = "model_blocked"
	CodeModelExpired            = "model_expired"
	CodeModelDeprecated         = "model_deprecated" // auto only
	CodeModelDegraded           = "model_degraded"   // auto only
	CodeDataPolicyZDR           = "data_policy_zdr"
	CodeDataPolicyCollection    = "data_policy_collection"
	CodeRegionNotAllowed        = "region_not_allowed"
	CodeRegionUnpinnable        = "region_unpinnable_on_aggregator"
	CodePreviewBlocked          = "preview_blocked_in_env"
	CodeNoProviderMatches       = "no_provider_matches"
	CodePriceCap                = "price_cap"
	CodeQuantization            = "quantization"
	CodeMissingParameter        = "missing_parameter"
	CodeBillingModeRestricted   = "billing_mode_restricted"
	CodeToolHistoryIncompatible = "tool_history_incompatible"
)
