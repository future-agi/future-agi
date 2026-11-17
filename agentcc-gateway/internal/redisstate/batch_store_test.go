package redisstate

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/batch"
)

const (
	testBatchOrg   = "org-a"
	testOtherOrg   = "org-b"
	testBatchOwner = "replica-a"
	testBatchLease = 10 * time.Second
	testBatchTTL   = time.Hour
)

// batchStoreTestRedis connects to the test Redis: each connect is a client
// of its own, as each replica of a cluster has. Every key is under prefix,
// and is removed after the test.
func batchStoreTestRedis(t *testing.T) (connect func() *Client, prefix string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db := 0
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		var err error
		if db, err = strconv.Atoi(v); err != nil || db < 0 {
			t.Fatalf("TEST_REDIS_DB=%q is not a database number", v)
		}
	}
	dial := func() *Client {
		client, err := NewClient(Config{Address: addr, DB: db, Timeout: time.Second})
		if err != nil {
			t.Skipf("skipping: Redis not available at %s: %v", addr, err)
		}
		return client
	}
	prefix = fmt.Sprintf("test:batch:%s:%d:", t.Name(), time.Now().UnixNano())
	admin := dial()
	var clients []*Client
	t.Cleanup(func() {
		ctx := context.Background()
		iter := admin.Redis().Scan(ctx, 0, prefix+"*", 100).Iterator()
		for iter.Next(ctx) {
			admin.Redis().Del(ctx, iter.Val())
		}
		admin.Close()
		for _, client := range clients {
			client.Close()
		}
	})
	return func() *Client {
		client := dial()
		clients = append(clients, client)
		return client
	}, prefix
}

// newTestBatchStore is a store under prefix on a client of its own, and
// that client.
func newTestBatchStore(t *testing.T) (*BatchStore, *Client, string) {
	t.Helper()
	connect, prefix := batchStoreTestRedis(t)
	client := connect()
	return NewBatchStore(client, prefix), client, prefix
}

// testBatchRecord is a stored record of id for orgID with status, code and
// completed items.
func testBatchRecord(id, orgID, status, code string, completed int) []byte {
	return []byte(fmt.Sprintf(`{"batch_id":%q,"status":%q,"error_code":%q,"org_id":%q,"total":3,"counts":{"completed":%d,"failed":0,"cancelled":0}}`, id, status, code, orgID, completed))
}

// testBatchWrite is the owner's write of id with status and completed items.
func testBatchWrite(id, status string, completed int, final bool, results map[int][]byte) batch.Write {
	return batch.Write{
		ID:      id,
		OrgID:   testBatchOrg,
		Owner:   testBatchOwner,
		Status:  status,
		Record:  testBatchRecord(id, testBatchOrg, status, "", completed),
		Lost:    testBatchRecord(id, testBatchOrg, "failed", batch.OwnerLost, completed),
		Results: results,
		Final:   final,
		TTL:     testBatchTTL,
	}
}

// createTestBatch creates a processing batch id of orgID, leased by the
// test owner.
func createTestBatch(t *testing.T, store *BatchStore, id, orgID string) {
	t.Helper()
	if err := store.Create(context.Background(), id, orgID, testBatchOwner, testBatchRecord(id, orgID, "processing", "", 0), testBatchLease, testBatchTTL); err != nil {
		t.Fatalf("create %s: %v", id, err)
	}
}

func dropKey(t *testing.T, client *Client, key string) {
	t.Helper()
	if err := client.Redis().Del(context.Background(), key).Err(); err != nil {
		t.Fatalf("removing %s: %v", key, err)
	}
}

func storedRecord(t *testing.T, client *Client, key string) []byte {
	t.Helper()
	raw, err := client.Redis().Get(context.Background(), key).Bytes()
	if err != nil {
		t.Fatalf("reading %s: %v", key, err)
	}
	return raw
}

func keyCount(t *testing.T, client *Client, keys ...string) int64 {
	t.Helper()
	n, err := client.Redis().Exists(context.Background(), keys...).Result()
	if err != nil {
		t.Fatal(err)
	}
	return n
}

// While the owner holds the lease, a write stores its record and results
// and leaves the lease and a cancel request; its final write ends both.
func TestBatchStoreSaveWhileTheLeaseIsHeldStoresTheOwnersRecord(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-held", testBatchOrg)
	if err := store.RequestCancel(ctx, "batch-held", testBatchTTL); err != nil {
		t.Fatal(err)
	}

	progress := testBatchWrite("batch-held", "processing", 1, false, map[int][]byte{0: []byte(`{"status":"success"}`)})
	if kept, err := store.Save(ctx, progress); err != nil || !kept {
		t.Fatalf("a progress write under the lease = %v, %v; want kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-held"); !bytes.Equal(got, progress.Record) {
		t.Fatalf("stored record %s, want %s", got, progress.Record)
	}
	if n := keyCount(t, client, prefix+"batch-held:lease", prefix+"batch-held:cancel"); n != 2 {
		t.Fatalf("a progress write left %d of the lease and the cancel request, want both", n)
	}

	final := testBatchWrite("batch-held", "completed", 3, true, nil)
	if kept, err := store.Save(ctx, final); err != nil || !kept {
		t.Fatalf("the final write under the lease = %v, %v; want kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-held"); !bytes.Equal(got, final.Record) {
		t.Fatalf("stored record %s, want %s", got, final.Record)
	}
	if n := keyCount(t, client, prefix+"batch-held:lease", prefix+"batch-held:cancel"); n != 0 {
		t.Fatalf("the final write left %d of the lease and the cancel request, want none", n)
	}
	if got, err := client.Redis().HGet(ctx, prefix+"batch-held:results", "0").Result(); err != nil || got != `{"status":"success"}` {
		t.Fatalf("stored result 0 = %q, %v", got, err)
	}
}

// Once the owner's lease is gone, its write of a batch every replica reads
// as lost stores the batch as failed with owner_lost, with the write's
// counts and results: a late completion never replaces that outcome.
func TestBatchStoreSaveAfterTheLeaseIsGoneStoresAProcessingBatchAsLost(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-lost", testBatchOrg)
	dropKey(t, client, prefix+"batch-lost:lease")

	late := testBatchWrite("batch-lost", "completed", 3, true, map[int][]byte{2: []byte(`{"status":"success"}`)})
	kept, err := store.Save(ctx, late)
	if err != nil || kept {
		t.Fatalf("a late final write = %v, %v; want not kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-lost"); !bytes.Equal(got, late.Lost) {
		t.Fatalf("stored record %s, want the lost one %s", got, late.Lost)
	}
	if got, err := client.Redis().HGet(ctx, prefix+"batch-lost:results", "2").Result(); err != nil || got != `{"status":"success"}` {
		t.Fatalf("stored result 2 = %q, %v; want the late result", got, err)
	}
	if kept, err := store.Save(ctx, late); err != nil || kept {
		t.Fatalf("the late write again = %v, %v; want not kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-lost"); !bytes.Equal(got, late.Lost) {
		t.Fatalf("stored record after the write again %s, want the lost one", got)
	}
}

// Once the owner's lease is gone, an outcome stored while it was held
// stands: a write with that status still updates its counts (a final write
// tried again after its answer was lost stays completed), and a write with
// another status changes nothing of the record.
func TestBatchStoreSaveAfterTheLeaseIsGoneKeepsAnOutcomeStoredUnderIt(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-done", testBatchOrg)
	cancelled := testBatchWrite("batch-done", "cancelled", 1, false, nil)
	if kept, err := store.Save(ctx, cancelled); err != nil || !kept {
		t.Fatalf("the cancel write under the lease = %v, %v; want kept", kept, err)
	}
	dropKey(t, client, prefix+"batch-done:lease")

	again := testBatchWrite("batch-done", "cancelled", 2, true, nil)
	if kept, err := store.Save(ctx, again); err != nil || !kept {
		t.Fatalf("a final write with the stored status = %v, %v; want kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-done"); !bytes.Equal(got, again.Record) {
		t.Fatalf("stored record %s, want %s", got, again.Record)
	}
	other := testBatchWrite("batch-done", "completed", 3, true, nil)
	if kept, err := store.Save(ctx, other); err != nil || kept {
		t.Fatalf("a final write with another status = %v, %v; want not kept", kept, err)
	}
	if got := storedRecord(t, client, prefix+"batch-done"); !bytes.Equal(got, again.Record) {
		t.Fatalf("stored record %s, want it unchanged %s", got, again.Record)
	}

	createTestBatch(t, store, "batch-retried", testBatchOrg)
	completed := testBatchWrite("batch-retried", "completed", 3, true, nil)
	for try := 1; try <= 2; try++ {
		if kept, err := store.Save(ctx, completed); err != nil || !kept {
			t.Fatalf("final write %d = %v, %v; want kept", try, kept, err)
		}
	}
	if got := storedRecord(t, client, prefix+"batch-retried"); !bytes.Equal(got, completed.Record) {
		t.Fatalf("stored record %s, want %s", got, completed.Record)
	}
}

// A write of a batch that has expired stores nothing.
func TestBatchStoreSaveLeavesAnExpiredBatchAlone(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	late := testBatchWrite("batch-expired", "completed", 3, true, map[int][]byte{0: []byte(`{"status":"success"}`)})
	if kept, err := store.Save(context.Background(), late); err != nil || kept {
		t.Fatalf("a write of an expired batch = %v, %v; want not kept", kept, err)
	}
	if n := keyCount(t, client, prefix+"batch-expired", prefix+"batch-expired:results"); n != 0 {
		t.Fatalf("a write of an expired batch stored %d keys, want none", n)
	}
}

// Renewing holds the lease while it exists and reports a cancel request; a
// lease that is gone stays gone.
func TestBatchStoreRenewNeverBringsBackAnExpiredLease(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-renew", testBatchOrg)
	if err := store.RequestCancel(ctx, "batch-renew", testBatchTTL); err != nil {
		t.Fatal(err)
	}
	if held, cancel, err := store.Renew(ctx, "batch-renew", testBatchOrg, testBatchOwner, testBatchLease, testBatchTTL); err != nil || !held || !cancel {
		t.Fatalf("renew = held %v, cancel %v, %v; want held with a cancel request", held, cancel, err)
	}
	dropKey(t, client, prefix+"batch-renew:lease")
	if held, _, err := store.Renew(ctx, "batch-renew", testBatchOrg, testBatchOwner, testBatchLease, testBatchTTL); err != nil || held {
		t.Fatalf("renew of a lease that is gone = held %v, %v; want not held", held, err)
	}
	if n := keyCount(t, client, prefix+"batch-renew:lease"); n != 0 {
		t.Fatal("renewing brought back a lease that was gone")
	}
}

// List reads one org's batches and ListAll every org's, each record with
// whether its lease is held; a batch whose record has expired leaves both.
func TestBatchStoreListsAnOrgsBatchesOrEveryOrgsAndForgetsExpiredOnes(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-a", testBatchOrg)
	createTestBatch(t, store, "batch-b", testOtherOrg)
	dropKey(t, client, prefix+"batch-b:lease")

	held := func(stored []batch.StoredBatch) map[string]bool {
		out := map[string]bool{}
		for _, s := range stored {
			var rec struct {
				ID string `json:"batch_id"`
			}
			if err := json.Unmarshal(s.Record, &rec); err != nil {
				t.Fatal(err)
			}
			out[rec.ID] = s.LeaseHeld
		}
		return out
	}
	if got, err := store.List(ctx, testBatchOrg); err != nil || fmt.Sprint(held(got)) != "map[batch-a:true]" {
		t.Fatalf("the org's listing = %v, %v; want batch-a with its lease", held(got), err)
	}
	if got, err := store.ListAll(ctx); err != nil || fmt.Sprint(held(got)) != "map[batch-a:true batch-b:false]" {
		t.Fatalf("the listing of every org = %v, %v; want batch-a leased and batch-b not", held(got), err)
	}

	dropKey(t, client, prefix+"batch-a")
	if got, err := store.List(ctx, testBatchOrg); err != nil || len(got) != 0 {
		t.Fatalf("the org's listing after its batch expired = %v, %v; want nothing", held(got), err)
	}
	if got, err := store.ListAll(ctx); err != nil || fmt.Sprint(held(got)) != "map[batch-b:false]" {
		t.Fatalf("the listing of every org after a batch expired = %v, %v; want batch-b", held(got), err)
	}
	for _, set := range []string{prefix + "org:" + testBatchOrg, prefix + "all"} {
		if in, err := client.Redis().SIsMember(ctx, set, "batch-a").Result(); err != nil || in {
			t.Errorf("%s still holds the expired batch: %v, %v", set, in, err)
		}
	}
}

// Looking up batches that do not exist is an answer, not a Redis failure: it
// never opens the circuit the gateway's other Redis state shares.
func TestBatchStoreLookupsOfMissingBatchesKeepTheCircuitClosed(t *testing.T) {
	store, client, _ := newTestBatchStore(t)
	for i := 0; i < 5; i++ {
		if _, found, err := store.Load(context.Background(), fmt.Sprintf("batch-missing-%d", i)); err != nil || found {
			t.Fatalf("load of a missing batch = %v, %v; want not found, nil", found, err)
		}
	}
	if !client.Available() {
		t.Fatal("lookups of missing batches opened the Redis circuit")
	}
}

// Without Redis every read and write is an error, and a lookup never
// answers not found.
func TestBatchStoreAnswersAnErrorWithoutRedis(t *testing.T) {
	store, client, _ := newTestBatchStore(t)
	client.Close()
	ctx := context.Background()
	if err := store.Create(ctx, "batch-x", testBatchOrg, testBatchOwner, testBatchRecord("batch-x", testBatchOrg, "processing", "", 0), testBatchLease, testBatchTTL); err == nil {
		t.Error("create without Redis succeeded")
	}
	if _, found, err := store.Load(ctx, "batch-x"); err == nil || found {
		t.Errorf("load without Redis = found %v, %v; want an error", found, err)
	}
	if _, err := store.List(ctx, testBatchOrg); err == nil {
		t.Error("the org's listing without Redis succeeded")
	}
	if _, err := store.ListAll(ctx); err == nil {
		t.Error("the listing of every org without Redis succeeded")
	}
	if err := store.RequestCancel(ctx, "batch-x", testBatchTTL); err == nil {
		t.Error("a cancel request without Redis succeeded")
	}
}

// A finished batch keeps its record, results and listing entries for the
// retention after its final write, and holds no lease.
func TestBatchStoreKeepsAFinishedBatchForTheRetentionWithoutALease(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-kept", testBatchOrg)
	if kept, err := store.Save(ctx, testBatchWrite("batch-kept", "completed", 3, true, map[int][]byte{0: []byte(`{}`)})); err != nil || !kept {
		t.Fatalf("final write = %v, %v", kept, err)
	}
	for _, key := range []string{prefix + "batch-kept", prefix + "batch-kept:results", prefix + "org:" + testBatchOrg, prefix + "all"} {
		if ttl, err := client.Redis().PTTL(ctx, key).Result(); err != nil || ttl <= 59*time.Minute || ttl > time.Hour {
			t.Errorf("%s is kept %v, %v; want the 1 h retention", key, ttl, err)
		}
	}
	if n := keyCount(t, client, prefix+"batch-kept:lease", prefix+"batch-kept:cancel"); n != 0 {
		t.Fatalf("a finished batch keeps %d of its lease and cancel keys, want none", n)
	}
}

// A final write carries every result of a large batch.
func TestBatchStoreFinalWriteTakesThousandsOfResults(t *testing.T) {
	store, client, prefix := newTestBatchStore(t)
	ctx := context.Background()
	createTestBatch(t, store, "batch-large", testBatchOrg)
	results := make(map[int][]byte, 10000)
	for i := 0; i < 10000; i++ {
		results[i] = []byte(`{"status":"success"}`)
	}
	if kept, err := store.Save(ctx, testBatchWrite("batch-large", "completed", 10000, true, results)); err != nil || !kept {
		t.Fatalf("final write of 10000 results = %v, %v", kept, err)
	}
	if n, err := client.Redis().HLen(ctx, prefix+"batch-large:results").Result(); err != nil || n != 10000 {
		t.Fatalf("stored %d results, %v; want 10000", n, err)
	}
}
