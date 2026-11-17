package auth

import (
	"slices"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
)

// The metadata tests in this package name the reserved entries one by one; a
// name added to the key store's list must be added to them too.
func TestPlugin_ReservedNameListsMatchTheKeyStore(t *testing.T) {
	authority := authpkg.AuthorityMetadataKeys()
	slices.Sort(authority)
	if !slices.Equal(authority, authorityNames) {
		t.Errorf("authority metadata = %v, the tests cover %v", authority, authorityNames)
	}
	var labels []string
	for _, name := range authpkg.ReservedMetadataKeys() {
		if !slices.Contains(authority, name) {
			labels = append(labels, name)
		}
	}
	if !slices.Equal(labels, reservedLabelNames) {
		t.Errorf("reserved non-authority metadata = %v, the tests cover %v", labels, reservedLabelNames)
	}
}
