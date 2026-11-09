//go:build cataloguepreflighttest

// Package identity holds exact catalogue fixture identities, not address policy.
// It is separate from the fixture runtime so the test-only egress seam can share
// an identity without importing the fixture that constructs its dialer.
package identity

// MetadataIP is the logical metadata answer used by the synthetic fixture.
// Whether this address may be dialled remains exclusively netguard's decision.
const MetadataIP = "169.254.169.254"
