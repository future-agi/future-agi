package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"testing"
	"time"
)

// The oldest pending record stays pollable while >1000 later terminal records
// occupy the global Redis index. Listing must select status before its limit.
func TestW12ApprovalPendingBehindTerminalRecords(t *testing.T) {
	now := time.Now()
	m, org := w5Manager(t, func() time.Time { return now })
	cc := w5Call(org)
	ttlSeconds := 3600
	cc.OrgConfig.MCP.VirtualServers["review"].Approval.TTLSeconds = &ttlSeconds
	pending := w5Pending(t, m, cc)
	ctx := context.Background()
	for i := 0; i < 2001; i++ {
		status := "used"
		if i%2 == 0 {
			status = "denied"
		}
		id := fmt.Sprintf("z-terminal-%04d", i)
		raw, err := json.Marshal(approvalState{Record: ApprovalRecord{
			ID: id, OrgID: org, Status: status, RequestedAt: now,
			ExpiresAt: now.Add(time.Hour),
		}})
		if err != nil {
			t.Fatal(err)
		}
		// Equal policy/retention TTL; z also sorts after the earlier ULID if
		// Redis TIME ties. No sleeps are needed to force the adversarial order.
		if err := m.store.Create(ctx, "mapr:{"+org+"}:"+id, approvalIndex(org), raw, time.Hour+approvalRetention); err != nil {
			t.Fatal(err)
		}
	}
	if row, err := m.Poll(ctx, pending, cc.Principal, cc.VirtualServer); err != nil || row.Status != "pending" {
		t.Fatalf("old approval is not valid: status=%s err=%v", row.Status, err)
	}
	rows, err := m.List(ctx, org, "pending")
	if err != nil || len(rows) != 1 || rows[0].ID != pending {
		t.Fatalf("pending disappeared behind terminal rows: count=%d err=%v", len(rows), err)
	}
}

// Pure pager fixture: List deliberately exposes only the legacy first page.
// This also pins derived expiry filtering and failure (never partial success).
type w12ApprovalPages struct {
	rows   [][]byte
	calls  int
	failAt int
}

func (s *w12ApprovalPages) Create(context.Context, string, string, []byte, time.Duration) error {
	panic("unused Create")
}
func (s *w12ApprovalPages) Load(context.Context, string) ([]byte, bool, error) {
	panic("unused Load")
}
func (s *w12ApprovalPages) CompareSwap(context.Context, string, []byte, []byte, time.Time) (bool, error) {
	panic("unused CompareSwap")
}
func (s *w12ApprovalPages) List(ctx context.Context, index string) ([][]byte, error) {
	rows, _, err := s.ListPage(ctx, index, "", 1000)
	return rows, err
}
func (s *w12ApprovalPages) ListPage(_ context.Context, _ string, cursor string, limit int) ([][]byte, string, error) {
	s.calls++
	if s.calls == s.failAt {
		return nil, "", errors.New("synthetic later-page outage")
	}
	start := 0
	if cursor != "" {
		var err error
		start, err = strconv.Atoi(cursor)
		if err != nil {
			return nil, "", err
		}
	}
	end := min(start+limit, len(s.rows))
	next := ""
	if end < len(s.rows) {
		next = strconv.Itoa(end)
	}
	return s.rows[start:end], next, nil
}

func TestW12ApprovalFiltersBeforeResultLimit(t *testing.T) {
	now := time.Now()
	for _, status := range []string{"pending", "approved", "denied", "used", "expired"} {
		t.Run(status, func(t *testing.T) {
			store := &w12ApprovalPages{}
			add := func(id, state string, expires time.Time) {
				raw, err := json.Marshal(approvalState{Record: ApprovalRecord{ID: id, OrgID: "org", Status: state, ExpiresAt: expires}})
				if err != nil {
					t.Fatal(err)
				}
				store.rows = append(store.rows, raw)
			}
			other := "used"
			if status == "used" {
				other = "denied"
			}
			for i := 0; i < 2001; i++ {
				add(fmt.Sprintf("other-%d", i), other, now.Add(time.Hour))
			}
			storedStatus, expires := status, now.Add(time.Hour)
			if status == "expired" {
				storedStatus, expires = "pending", now
			}
			for i := 0; i < 1001; i++ {
				add(fmt.Sprintf("wanted-%d", i), storedStatus, expires)
			}
			m := NewApprovalManager(store, nil, func() time.Time { return now })
			rows, err := m.List(context.Background(), "org", status)
			if err != nil || len(rows) != 1000 {
				t.Fatalf("filtered count=%d err=%v", len(rows), err)
			}
			for i, row := range rows {
				if row.ID != fmt.Sprintf("wanted-%d", i) || row.Status != status {
					t.Fatalf("row %d has ID=%s status=%s", i, row.ID, row.Status)
				}
			}
			if store.calls != 4 {
				t.Fatalf("page calls=%d, want 4 including two filtered-empty pages", store.calls)
			}
			// Do not return a misleading successful partial list on page failure.
			store.calls, store.failAt = 0, 4
			rows, err = m.List(context.Background(), "org", status)
			if !errors.Is(err, ErrApprovalUnavailable) || rows != nil {
				t.Fatalf("partial list escaped outage: count=%d err=%v", len(rows), err)
			}
		})
	}
}
