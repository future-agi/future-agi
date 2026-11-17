package redisstate

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// mcpSessionTestStore is an MCPSessionStore on the test Redis
// (TEST_REDIS_ADDR; TEST_REDIS_DB, else this lane's DB 5) under the prefix
// test:M12:<test>:<nonce>:, whose keys are deleted when the test ends (SCAN
// and DEL, never FLUSHDB). It skips without Redis.
func mcpSessionTestStore(t *testing.T) (*MCPSessionStore, redis.UniversalClient, string) {
	t.Helper()
	db := 5
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		db, _ = strconv.Atoi(v)
	}
	client, err := NewClient(Config{Address: redisAddr(), DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", redisAddr(), err)
	}
	prefix := fmt.Sprintf("test:M12:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return NewMCPSessionStore(client, prefix), rdb, prefix
}

const (
	mcpTestRecord = "mcps:{org-a}:h1"
	mcpTestEvents = "mcpev:{org-a}:h1"
)

func TestMCPSessionStoreRecordRoundTrip(t *testing.T) {
	store, rdb, prefix := mcpSessionTestStore(t)
	ctx := context.Background()

	if _, ok, err := store.Load(ctx, mcpTestRecord); ok || err != nil {
		t.Fatalf("Load of a missing record = %v %v", ok, err)
	}
	if err := store.Save(ctx, mcpTestRecord, []byte(`{"v":1}`), time.Minute); err != nil {
		t.Fatal(err)
	}
	data, ok, err := store.Load(ctx, mcpTestRecord)
	if err != nil || !ok || string(data) != `{"v":1}` {
		t.Fatalf("Load = %q %v %v", data, ok, err)
	}
	// The contract's key name, under the store's prefix, with the ttl.
	if pttl := rdb.PTTL(ctx, prefix+mcpTestRecord).Val(); pttl <= 0 || pttl > time.Minute {
		t.Fatalf("PTTL = %v, want within (0, 1m]", pttl)
	}
	if err := store.Delete(ctx, mcpTestRecord); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := store.Load(ctx, mcpTestRecord); ok {
		t.Fatal("the record outlived Delete")
	}
}

// Refresh renews a live record and its event log, and never writes back a
// record that ended (so a DELETE on one replica cannot be undone by a
// request in flight on another).
func TestMCPSessionStoreRefreshNeverResurrects(t *testing.T) {
	store, rdb, prefix := mcpSessionTestStore(t)
	ctx := context.Background()

	if alive, err := store.Refresh(ctx, mcpTestRecord, mcpTestEvents, []byte("x"), time.Minute); alive || err != nil {
		t.Fatalf("Refresh of a missing record = %v %v", alive, err)
	}
	if n := rdb.Exists(ctx, prefix+mcpTestRecord).Val(); n != 0 {
		t.Fatal("Refresh created a record")
	}

	if err := store.Save(ctx, mcpTestRecord, []byte("a"), time.Second); err != nil {
		t.Fatal(err)
	}
	if _, _, err := store.AppendSessionEvent(ctx, mcpTestRecord, mcpTestEvents, []byte("e"), 10, time.Second); err != nil {
		t.Fatal(err)
	}
	alive, err := store.Refresh(ctx, mcpTestRecord, mcpTestEvents, []byte("b"), time.Hour)
	if err != nil || !alive {
		t.Fatalf("Refresh of a live record = %v %v", alive, err)
	}
	if v := rdb.Get(ctx, prefix+mcpTestRecord).Val(); v != "b" {
		t.Fatalf("record = %q after Refresh", v)
	}
	for _, key := range []string{mcpTestRecord, mcpTestEvents} {
		if pttl := rdb.PTTL(ctx, prefix+key).Val(); pttl < 59*time.Minute {
			t.Fatalf("%s PTTL = %v after Refresh, want the new hour", key, pttl)
		}
	}

	if err := store.Delete(ctx, mcpTestRecord); err != nil {
		t.Fatal(err)
	}
	if alive, _ := store.Refresh(ctx, mcpTestRecord, mcpTestEvents, []byte("c"), time.Hour); alive {
		t.Fatal("Refresh brought back a deleted record")
	}
	if n := rdb.Exists(ctx, prefix+mcpTestRecord).Val(); n != 0 {
		t.Fatal("a deleted record was written back")
	}
}

// The event log keeps the last maxLen events in id order, replays strictly
// after an id, and takes nothing for a session that is gone.
func TestMCPSessionStoreEventLogCappedAndOrdered(t *testing.T) {
	store, rdb, prefix := mcpSessionTestStore(t)
	ctx := context.Background()

	if _, alive, err := store.AppendSessionEvent(ctx, mcpTestRecord, mcpTestEvents, []byte("e"), 5, time.Minute); alive || err != nil {
		t.Fatalf("append to a missing session = %v %v", alive, err)
	}
	if n := rdb.Exists(ctx, prefix+mcpTestEvents).Val(); n != 0 {
		t.Fatal("an event was logged for a missing session")
	}

	if err := store.Save(ctx, mcpTestRecord, []byte("r"), time.Minute); err != nil {
		t.Fatal(err)
	}
	var ids []string
	for i := 0; i < 7; i++ {
		id, alive, err := store.AppendSessionEvent(ctx, mcpTestRecord, mcpTestEvents, []byte(strconv.Itoa(i)), 5, time.Minute)
		if err != nil || !alive || id == "" {
			t.Fatalf("append %d = %q %v %v", i, id, alive, err)
		}
		ids = append(ids, id)
	}
	if n := rdb.XLen(ctx, prefix+mcpTestEvents).Val(); n != 5 {
		t.Fatalf("XLEN = %d, want the last 5", n)
	}
	if pttl := rdb.PTTL(ctx, prefix+mcpTestEvents).Val(); pttl <= 0 || pttl > time.Minute {
		t.Fatalf("event log PTTL = %v", pttl)
	}

	var got []string
	if err := store.SessionEventsAfter(ctx, mcpTestEvents, ids[3], 10, func(id string, data []byte) {
		got = append(got, id+"="+string(data))
	}); err != nil {
		t.Fatal(err)
	}
	if want := []string{ids[4] + "=4", ids[5] + "=5", ids[6] + "=6"}; fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("after %s: %v, want %v", ids[3], got, want)
	}
	got = nil
	_ = store.SessionEventsAfter(ctx, mcpTestEvents, ids[3], 2, func(id string, _ []byte) { got = append(got, id) })
	if len(got) != 2 || got[0] != ids[4] {
		t.Fatalf("limit 2: %v", got)
	}

	if err := store.DeleteSessionEvents(ctx, mcpTestEvents); err != nil {
		t.Fatal(err)
	}
	if n := rdb.Exists(ctx, prefix+mcpTestEvents).Val(); n != 0 {
		t.Fatal("the event log outlived DeleteSessionEvents")
	}
}
