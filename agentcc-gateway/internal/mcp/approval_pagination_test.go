package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
)

// Interface assertions make the missing additive capability an executable red,
// rather than a compile failure on the original authority implementation.
type approvalPageReader interface {
	ListPage(context.Context, string, string, string, int) ([]ApprovalRecord, string, error)
	Get(context.Context, string, string) (ApprovalRecord, error)
}

func TestW11MixedTTLExhaustiveApprovals(t *testing.T) {
	m, org := w5Manager(t, nil)
	reader, ok := any(m).(approvalPageReader)
	if !ok {
		t.Fatal("authoritative paginated list and by-ID lookup missing")
	}
	ctx := context.Background()
	ids := make([]string, 1002)
	calls := make([]*CallContext, len(ids))
	for i := range ids {
		cc := w5Call(org)
		ttl := 600
		if i < 2 {
			ttl = 120
		}
		raw := fmt.Sprintf(`{"mcp":{"servers":{"mock":{}},"virtual_servers":{"review":{"id":"vs-review","members":["mock"],"approval":{"require":"listed","tools":["mock/*"],"validity":"once","ttl_seconds":%d}}}}}`, ttl)
		if err := json.Unmarshal([]byte(raw), cc.OrgConfig); err != nil {
			t.Fatal(err)
		}
		cc.Args = json.RawMessage(fmt.Sprintf(`{"v":%d}`, i))
		calls[i] = cc
		ids[i] = w5Pending(t, m, cc)
	}
	first, cursor, err := reader.ListPage(ctx, org, "", "", 1000)
	if err != nil || len(first) != 1000 || cursor == "" {
		t.Fatalf("first page rows=%d cursor=%t err=%v", len(first), cursor != "", err)
	}
	for _, row := range first {
		if row.ID == ids[0] || row.ID == ids[1] {
			t.Fatal("short-TTL records must be outside the old window")
		}
	}
	// Consumption is a real Redis CAS by a second manager over the same store.
	other := NewApprovalManager(m.store, m.codec, nil)
	w5Approve(t, other, org, ids[1])
	w5Retry(calls[1], ids[1])
	if denial := other.Check(calls[1]); denial != nil {
		t.Fatal(denial)
	}
	tail, next, err := reader.ListPage(ctx, org, "", cursor, 1000)
	if err != nil || len(tail) != 2 || next != "" {
		t.Fatalf("tail rows=%d cursor=%q err=%v", len(tail), next, err)
	}
	seen := map[string]string{}
	for _, row := range append(first, tail...) {
		seen[row.ID] = row.Status
	}
	if len(seen) != 1002 || seen[ids[0]] != "pending" || seen[ids[1]] != "used" {
		t.Fatalf("coverage=%d pending=%s used=%s", len(seen), seen[ids[0]], seen[ids[1]])
	}
	for i, status := range []string{"pending", "used"} {
		row, err := reader.Get(ctx, org, ids[i])
		if err != nil || row.Status != status {
			t.Fatalf("lookup %d: %+v %v", i, row, err)
		}
		if _, err := reader.Get(ctx, "foreign", ids[i]); err != ErrApprovalNotFound {
			t.Fatalf("foreign detail err=%v", err)
		}
	}
	if _, _, err := reader.ListPage(ctx, "foreign", "", cursor, 1000); err != ErrApprovalInvalid {
		t.Fatalf("foreign cursor err=%v", err)
	}
}
