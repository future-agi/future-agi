package server

import (
	"slices"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A name added to the key store's reserved list must be added to the update
// test too.
func TestReservedNamesButOrgIDMatchTheKeyStore(t *testing.T) {
	want := slices.DeleteFunc(auth.ReservedMetadataKeys(), func(name string) bool { return name == "org_id" })
	if !slices.Equal(reservedNamesButOrgID, want) {
		t.Errorf("the update test covers %v, the key store reserves %v (and org_id)", reservedNamesButOrgID, want)
	}
}
