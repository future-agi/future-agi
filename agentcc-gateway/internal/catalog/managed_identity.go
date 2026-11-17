package catalog

import "strings"

// VariantBase is the exact base ID of a recognized variant. Unlike Canonical
// it does not guess model identity, fold dates or consult historical aliases.
// Policy denies inherit to variants; allowlists do not.
func VariantBase(model string) string { return stripVariant(model) }

// ManagedCanonical resolves identity for serving, not historical catalogue
// grouping. An unlisted date is never stripped. Historical moving aliases
// (-latest) and host-specific speed variants (-turbo) are merge-only; their
// exact IDs can still be served when the live catalogue actually lists them.
func ManagedCanonical(providerID, model string) string {
	id := strings.ToLower(strings.TrimSpace(model))
	if HasVariant(id) {
		return ""
	}
	if canon, ok := knownCanonical(id); ok {
		return canon
	}
	if p := strings.ToLower(providerID) + "/"; strings.HasPrefix(id, p) {
		id = strings.TrimPrefix(id, p)
	}
	vendor, rest := vendorAndModel(providerID, id)
	full := vendor + "/" + rest
	for _, candidate := range []string{id, full, dotVersions(full), dottedNumber.ReplaceAllString(full, "$1-$2")} {
		if canon, ok := knownCanonical(candidate); ok {
			return canon
		}
	}
	if strings.HasSuffix(rest, "-latest") || strings.HasSuffix(rest, "-turbo") {
		return ""
	}
	for _, candidate := range []string{id, rest, full} {
		if to, ok := aliasTable()[candidate]; ok {
			return to
		}
	}
	if datedSuffix.MatchString(rest) {
		return ""
	}
	return full
}
