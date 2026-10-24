package auth

import (
	"sort"
	"testing"
)

// The auth plugin writes the authority entries after the labels and drops
// every other reserved name, so every authority entry is reserved: a tenant
// never sets one, and none is copied as a label.
func TestAuthorityMetadataKeysAreReserved(t *testing.T) {
	authority := AuthorityMetadataKeys()
	if len(authority) == 0 {
		t.Fatal("no authority metadata keys")
	}
	for _, name := range authority {
		if !IsReservedMetadataKey(name) {
			t.Errorf("authority metadata %q is not reserved", name)
		}
	}
	reserved := ReservedMetadataKeys()
	if !sort.StringsAreSorted(reserved) || len(reserved) != len(reservedMetadataKeys) {
		t.Errorf("ReservedMetadataKeys() = %v", reserved)
	}
	authority[0] = "mutated"
	if AuthorityMetadataKeys()[0] == "mutated" {
		t.Error("AuthorityMetadataKeys must return a copy")
	}
}
