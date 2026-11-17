package models

// BillingPlatformFunded is the billing of a key the platform pays for itself,
// such as a key it mints for one of its own runs. The auth plugin writes it to
// the request as MetadataKeyBilling, only from the key's platform-set
// "billing" metadata, which only the platform sets.
const BillingPlatformFunded = "platform_funded"

// MetadataKeyBilling is the request metadata entry that carries a key's
// billing. Request metadata never sets it: every key_ name is dropped from it.
const MetadataKeyBilling = "key_billing"

// PlatformFunded reports whether rc's key is platform-funded. Its requests are
// logged with their real cost, as every request is, but are not counted in or
// refused by the org's gateway budgets, and are not held on or settled to the
// org's managed credit. The key's own USD cap still applies.
func PlatformFunded(rc *RequestContext) bool {
	return rc != nil && rc.Metadata[MetadataKeyBilling] == BillingPlatformFunded
}

// HonouredKeyBilling returns the key billing values this build applies, in a
// new slice. GET /-/health reports it as platform_key_billing.
func HonouredKeyBilling() []string {
	return []string{BillingPlatformFunded}
}
