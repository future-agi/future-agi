package server

import (
	"runtime"
	"strings"
	"testing"
	"time"
)

const mib = 1 << 20

// The budget and the caps follow the memory limit, are never raised to fit
// a request, and lower the caps (saying so) when the budget cannot hold one
// request at the cap.
func TestResolveIngestLimits(t *testing.T) {
	cases := []struct {
		name               string
		cfg                Config
		memLimit           int64
		budget, body, recv int64
		source, note       string
	}{
		{"no memory limit", Config{}, 0, 256 * mib, 16 * mib, 16 * mib, "default", "no memory limit"},
		{"standalone GOMEMLIMIT=256MiB", Config{}, 256 * mib, 108 * mib, 16 * mib, 16 * mib, "GOMEMLIMIT", ""},
		{"Helm GOMEMLIMIT=900MiB", Config{}, 900 * mib, (900*mib - 32*mib - 2*3*(900*mib/64)) / 2, 16 * mib, 16 * mib, "GOMEMLIMIT", ""},
		{"a 1 GiB container (90%)", Config{}, 1024 * mib * 9 / 10, (1024*mib*9/10 - 32*mib - 2*3*(1024*mib*9/10/64)) / 2, 16 * mib, 16 * mib, "GOMEMLIMIT", ""},
		{"GOMEMLIMIT=64MiB lowers the caps", Config{}, 64 * mib, 14 * mib, 14*mib/3/2 - readChunk, 14 * mib / 3 * 4 / grpcLeasePer4, "GOMEMLIMIT", "body cap lowered"},
		{"configured budget", Config{IngestBudgetMiB: 200}, 256 * mib, 200 * mib, 16 * mib, 16 * mib, "FI_INGEST_BUDGET_MIB", ""},
		{"receive cap over the budget", Config{GRPCMaxRecvMiB: 64}, 256 * mib, 108 * mib, 16 * mib, 108 * mib / 3 * 4 / grpcLeasePer4, "GOMEMLIMIT", "gRPC receive cap lowered"},
		{"smaller body cap", Config{MaxBodyMiB: 4}, 256 * mib, 108 * mib, 4 * mib, 4 * mib, "GOMEMLIMIT", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := resolveIngestLimits(tc.cfg, tc.memLimit)
			if l.maxWait != 2*time.Second {
				t.Errorf("production default lease wait = %v, want 2s", l.maxWait)
			}
			if l.budget != tc.budget || l.maxBody != tc.body || l.maxRecv != tc.recv || l.source != tc.source {
				t.Errorf("budget %d body %d recv %d source %q; want %d %d %d %q",
					l.budget, l.maxBody, l.maxRecv, l.source, tc.budget, tc.body, tc.recv, tc.source)
			}
			if l.wire+l.work != l.budget || l.wire != l.budget/3 {
				t.Errorf("pools wire %d + work %d, want a third and the rest of %d", l.wire, l.work, l.budget)
			}
			if 2*l.maxBody > l.wire || grpcWireLease(l.maxRecv) > l.wire {
				t.Errorf("a request at the cap does not fit the wire pool: body %d recv %d wire %d", l.maxBody, l.maxRecv, l.wire)
			}
			if l.maxEncoded != maxEncodedPerBody*l.maxBody {
				t.Errorf("stored-size limit %d, want %d x the body cap", l.maxEncoded, maxEncodedPerBody)
			}
			notes := strings.Join(l.notes, "\n")
			if (tc.note == "") != (notes == "") || !strings.Contains(notes, tc.note) {
				t.Errorf("notes %q, want one containing %q", notes, tc.note)
			}
		})
	}
}

// The live-heap backstop reads the live heap at most once a second and is
// off without a memory limit.
func TestHeapGuard(t *testing.T) {
	if newHeapGuard(0).overLimit(time.Now()) {
		t.Error("with no memory limit the guard must be off")
	}
	runtime.GC() // so the live-heap metric has a value
	g := newHeapGuard(64 << 10)
	now := time.Now()
	if !g.overLimit(now) {
		t.Fatal("a live heap over 85% of a 64 KiB limit must trip the guard")
	}
	g.limit = 1 << 62
	if !g.overLimit(now.Add(500 * time.Millisecond)) {
		t.Error("the guard read the heap again within a second")
	}
	if g.overLimit(now.Add(1100 * time.Millisecond)) {
		t.Error("after a second the guard must read the heap again")
	}
}
