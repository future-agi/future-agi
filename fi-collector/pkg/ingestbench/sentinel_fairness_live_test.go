package ingestbench

import (
	"context"
	"testing"
	"time"
)

func TestLiveClickHouseSentinelPollingFairness(t *testing.T) {
	lt := requireLiveTarget(t)
	c := lt.apply(testConfig())
	c.RunID, c.ClickHouseURL = NewRunID(), lt.url
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	start := time.Now()
	writer := lt.fixtureWriter(t)
	// Only the newest identity is stored. The earlier 4,096 remain missing.
	if err := writer.Insert(ctx, []map[string]any{storedRow(c, maxPollIDsPerTick, start)}); err != nil {
		t.Fatal(err)
	}
	s := newRunState(c, start, false)
	for seq := int64(0); seq <= maxPollIDsPerTick; seq++ {
		s.registerSentinel(seq, start, start, start)
		s.sentinelFinished(seq, true)
	}
	ch := clickHouse{cfg: c, client: c.readClient(), headers: lt.headers}
	defer ch.client.CloseIdleConnections()
	s.pollOnce(ctx, ch)
	s.pollOnce(ctx, ch)
	if s.lagErrors != 0 || s.seen != 1 || s.sentinels[maxPollIDsPerTick].seenAt.IsZero() || len(s.pending) != maxPollIDsPerTick {
		t.Fatalf("real polling starved the stored tail: errors=%d seen=%d pending=%d context=%v", s.lagErrors, s.seen, len(s.pending), ctx.Err())
	}
	for seq, st := range s.pending {
		if st.lastMiss.IsZero() || !st.seenAt.IsZero() {
			t.Fatalf("missing sentinel %d has invalid bounds", seq)
		}
	}
	t.Logf("real ClickHouse: visible tail observed within two bounded ticks despite 4096 missing earlier identities; synthetic acknowledgements, run_id=%s", c.RunID)
}
