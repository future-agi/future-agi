package catalog

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR602CompactRouterQuote(t *testing.T) {
	// Historical serialized evidence stays compact, but it is not an active
	// admission card. 746g must refuse it even when the old flag is true.
	universe := `[{"id":"synthetic/target","prompt":"0.000001","completion":"0.000002"}]`
	digest := sha256.Sum256([]byte(universe))
	card := models.PriceCard{Inclusive: &models.InclusivePrice{Model: "openrouter/auto", Contract: RouterInclusiveContract, RouterUniverse: universe, RouterSnapshot: hex.EncodeToString(digest[:]), SnapshotModels: `["synthetic/target"]`}}
	m := &Model{ID: "openrouter/auto", InclusiveEnabled: true, Inclusive: card.Inclusive}
	if m.InclusivePrice(SourceOpenRouter) != nil || m.ManagedBillable(SourceOpenRouter) {
		t.Fatal("historical router quote became credit admission")
	}
	raw, err := json.Marshal(card.Inclusive)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("router_universe")) || bytes.Contains(raw, []byte("snapshot_models")) {
		t.Fatal("private catalogue universe repeated in public model quotes")
	}
	var quote map[string]any
	if json.Unmarshal(raw, &quote) != nil {
		t.Fatal("bad quote")
	}
	hash := sha256.Sum256([]byte(card.Inclusive.RouterUniverse))
	if quote["router_snapshot"] != hex.EncodeToString(hash[:]) {
		t.Fatal("compact quote must retain exact-universe digest")
	}
}
