package redisstate

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// managedTestStore is a ManagedCreditStore on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:managedcredits:<test>:<nonce>:, whose keys are
// deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips without
// Redis.
func managedTestStore(t *testing.T) (*ManagedCreditStore, redis.UniversalClient, string) {
	t.Helper()
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := NewClient(Config{Address: redisAddr(), DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", redisAddr(), err)
	}
	prefix := fmt.Sprintf("test:managedcredits:%s:%d:", t.Name(), time.Now().UnixNano())
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
	return NewManagedCreditStore(client, prefix), rdb, prefix
}

var bg = context.Background()

func mustGrant(t *testing.T, s *ManagedCreditStore, org string, micros, version, floor int64) billing.GrantResult {
	t.Helper()
	res, err := s.Grant(bg, org, billing.GrantPush{GrantedTotalMicros: micros, Version: version, SettledSpendFloorMicros: floor})
	if err != nil {
		t.Fatalf("grant: %v", err)
	}
	return res
}

func mustAccount(t *testing.T, s *ManagedCreditStore, org string) billing.Account {
	t.Helper()
	a, err := s.Account(bg, org)
	if err != nil {
		t.Fatalf("account: %v", err)
	}
	return a
}

func spendRecord(org, id string, billed int64) billing.Settlement {
	return billing.Settlement{RequestID: id, OrgID: org, BilledMicros: billed, UpstreamMicros: billed * 100 / 105,
		HoldMicros: 30_000, Model: "vendor/model", Provider: "openrouter", CostSource: "catalog", TsMs: 1792056612419}
}

// 50 concurrent reserves of $0.03 against $1.00: exactly 33 fit, and spent +
// held never exceeds granted while they and their settlements run.
func TestManagedCreditsConcurrentReserve(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-c", 1_000_000, 1, 0)

	var stop atomic.Bool
	var violations atomic.Int32
	watched := make(chan struct{})
	go func() {
		defer close(watched)
		for !stop.Load() {
			v, err := rdb.HMGet(bg, s.keys("org-c").acct, "granted", "spent", "held").Result()
			if err != nil {
				continue
			}
			granted, _ := strconv.ParseInt(fmt.Sprint(v[0]), 10, 64)
			spent, _ := strconv.ParseInt(fmt.Sprint(v[1]), 10, 64)
			held, _ := strconv.ParseInt(fmt.Sprint(v[2]), 10, 64)
			if spent+held > granted {
				violations.Add(1)
			}
		}
	}()

	var ok atomic.Int32
	var reserved sync.Map
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			id := fmt.Sprintf("req-%02d", i)
			res, err := s.Reserve(bg, "org-c", id, 30_000, time.Minute, billing.ReserveNew)
			if err != nil {
				t.Errorf("reserve %s: %v", id, err)
				return
			}
			if res.Status == billing.ReserveOK {
				ok.Add(1)
				reserved.Store(id, true)
			} else if res.Status != billing.ReserveInFlight {
				t.Errorf("reserve %s: %v, want ok or in_flight", id, res.Status)
			}
		}(i)
	}
	wg.Wait()
	if n := ok.Load(); n != 33 {
		t.Fatalf("%d of 50 holds of $0.03 fit in $1.00, want exactly 33", n)
	}
	a := mustAccount(t, s, "org-c")
	if a.HeldMicros != 990_000 || a.SpentMicros != 0 {
		t.Fatalf("account after the reserves = %+v, want 990000 held", a)
	}

	// Settle every hold for exactly its amount, concurrently.
	reserved.Range(func(id, _ any) bool {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if _, err := s.Settle(bg, "org-c", id, spendRecord("org-c", id, 30_000)); err != nil {
				t.Errorf("settle %s: %v", id, err)
			}
		}(id.(string))
		return true
	})
	wg.Wait()
	stop.Store(true)
	<-watched
	if v := violations.Load(); v != 0 {
		t.Fatalf("spent + held exceeded granted %d times", v)
	}
	if a := mustAccount(t, s, "org-c"); a.HeldMicros != 0 || a.SpentMicros != 990_000 {
		t.Fatalf("account after settling = %+v, want 990000 spent and nothing held", a)
	}
}

func TestSweepExpiredHolds(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-s", 100_000, 1, 0)
	if res, err := s.Reserve(bg, "org-s", "old", 90_000, 50*time.Millisecond, billing.ReserveNew); err != nil || res.Status != billing.ReserveOK {
		t.Fatalf("first reserve: %+v %v", res, err)
	}
	if res, _ := s.Reserve(bg, "org-s", "early", 90_000, time.Minute, billing.ReserveNew); res.Status != billing.ReserveInFlight {
		t.Fatalf("before expiry: %v, want in_flight", res.Status)
	}
	time.Sleep(120 * time.Millisecond)
	res, err := s.Reserve(bg, "org-s", "new", 90_000, time.Minute, billing.ReserveNew)
	if err != nil || res.Status != billing.ReserveOK || res.Account.HeldMicros != 90_000 {
		t.Fatalf("after expiry: %+v %v; the expired hold was not swept", res, err)
	}
	k := s.keys("org-s")
	if rdb.HExists(bg, k.holds, "old").Val() || rdb.ZScore(bg, k.holdexp, "old").Err() != redis.Nil {
		t.Fatal("the swept hold is still in holds or holdexp")
	}
}

func TestSettleIdempotentWithOutbox(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-i", 1_000_000, 1, 0)
	s.Reserve(bg, "org-i", "req-1", 40_000, time.Minute, billing.ReserveNew)
	k := s.keys("org-i")
	before := rdb.XLen(bg, k.outbox).Val()

	first, err := s.Settle(bg, "org-i", "req-1", spendRecord("org-i", "req-1", 11_815))
	if err != nil || !first.Applied {
		t.Fatalf("first settle: %+v %v", first, err)
	}
	second, err := s.Settle(bg, "org-i", "req-1", spendRecord("org-i", "req-1", 11_815))
	if err != nil || second.Applied {
		t.Fatalf("second settle: %+v %v, want a no-op", second, err)
	}
	if grew := rdb.XLen(bg, k.outbox).Val() - before; grew != 1 {
		t.Fatalf("outbox grew by %d, want exactly 1", grew)
	}
	a := mustAccount(t, s, "org-i")
	if a.SpentMicros != 11_815 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v, want spent 11815 and nothing held", a)
	}
	entry := rdb.XRange(bg, k.outbox, "-", "+").Val()[0].Values
	if entry["kind"] != "spend" || entry["billed_micros"] != "11815" || entry["request_id"] != "req-1" {
		t.Fatalf("outbox record = %v", entry)
	}
	if ttl := rdb.TTL(bg, k.settled("req-1")).Val(); ttl <= 6*24*time.Hour {
		t.Fatalf("settled marker TTL = %v, want about 7 days", ttl)
	}
	if !rdb.SIsMember(bg, s.outboxOrgsKey(), "org-i").Val() {
		t.Fatal("the org was not added to the outbox discovery set")
	}
}

func TestVoidReleases(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-v", 100_000, 1, 0)
	s.Reserve(bg, "org-v", "req-v", 60_000, time.Minute, billing.ReserveNew)
	rec := spendRecord("org-v", "req-v", 5_000)
	rec.HoldMicros = 60_000
	res, err := s.Void(bg, "org-v", "req-v", rec)
	if err != nil || !res.Applied || res.Account.HeldMicros != 0 || res.Account.SpentMicros != 0 {
		t.Fatalf("void: %+v %v", res, err)
	}
	entries := rdb.XRange(bg, s.keys("org-v").outbox, "-", "+").Val()
	if len(entries) != 1 {
		t.Fatalf("outbox has %d records, want one void", len(entries))
	}
	v := entries[0].Values
	if v["kind"] != "void" || v["billed_micros"] != "0" || v["upstream_micros"] != "0" || v["hold_micros"] != "60000" {
		t.Fatalf("void record = %v, want kind void, zero amounts and the released hold", v)
	}
	if again, _ := s.Void(bg, "org-v", "req-v", rec); again.Applied {
		t.Fatal("a second void wrote again")
	}
	if late, _ := s.Settle(bg, "org-v", "req-v", rec); late.Applied {
		t.Fatal("a settle after the void wrote again")
	}
	if a := mustAccount(t, s, "org-v"); a.AvailableMicros() != 100_000 {
		t.Fatalf("available after void = %d, want the whole grant", a.AvailableMicros())
	}
}

func TestGrantVersionMonotonic(t *testing.T) {
	s, _, _ := managedTestStore(t)
	if res := mustGrant(t, s, "org-g", 1_000_000, 3, 0); !res.Applied || res.Account.GrantedVersion != 3 {
		t.Fatalf("first push: %+v", res)
	}
	for _, v := range []int64{3, 2, 0} {
		if res := mustGrant(t, s, "org-g", 9_000_000, v, 0); res.Applied || res.Account.GrantedMicros != 1_000_000 {
			t.Fatalf("version %d over stored 3: %+v, want not applied", v, res)
		}
	}
	if res := mustGrant(t, s, "org-g", 2_000_000, 4, 0); !res.Applied || res.Account.GrantedMicros != 2_000_000 || res.Account.GrantedVersion != 4 {
		t.Fatalf("version 4: %+v", res)
	}
}

func TestGrantFloorRestoresSpent(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-f", 1_000_000, 1, 0)
	s.Reserve(bg, "org-f", "r", 10_000, time.Minute, billing.ReserveNew)
	s.Settle(bg, "org-f", "r", spendRecord("org-f", "r", 70_000))
	if res := mustGrant(t, s, "org-f", 1_000_000, 1, 50_000); res.Account.SpentMicros != 70_000 {
		t.Fatalf("a lower floor lowered spent: %+v", res)
	}

	// A Redis loss: the account hash is gone; RESERVE says no_account until
	// the drain re-pushes granted with the ledger's settled spend as floor.
	rdb.Del(bg, s.keys("org-f").acct)
	if res, _ := s.Reserve(bg, "org-f", "r2", 1, time.Minute, billing.ReserveNew); res.Status != billing.ReserveNoAccount {
		t.Fatalf("after the loss: %v, want no_account", res.Status)
	}
	res := mustGrant(t, s, "org-f", 1_000_000, 2, 70_000)
	if !res.Applied || !res.Account.Exists || res.Account.SpentMicros != 70_000 || res.Account.HeldMicros != 0 {
		t.Fatalf("re-push with floor: %+v, want the account back with spent at the floor", res)
	}
	if r, _ := s.Reserve(bg, "org-f", "r3", 10_000, time.Minute, billing.ReserveNew); r.Status != billing.ReserveOK {
		t.Fatalf("after the re-push: %v", r.Status)
	}
}

// A hold in flight when the account hash is lost is counted again when the
// account is re-pushed, so its settlement releases exactly what it held.
func TestGrantRecreateCountsHoldsInFlight(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-h", 1_000_000, 1, 0)
	s.Reserve(bg, "org-h", "in-flight", 30_000, time.Minute, billing.ReserveNew)
	rdb.Del(bg, s.keys("org-h").acct)
	if res := mustGrant(t, s, "org-h", 1_000_000, 2, 0); res.Account.HeldMicros != 30_000 {
		t.Fatalf("recreated account = %+v, want the in-flight hold of 30000 held", res.Account)
	}
	s.Reserve(bg, "org-h", "after", 20_000, time.Minute, billing.ReserveNew)
	res, _ := s.Settle(bg, "org-h", "in-flight", spendRecord("org-h", "in-flight", 25_000))
	if res.Account.HeldMicros != 20_000 || res.Account.SpentMicros != 25_000 {
		t.Fatalf("after settling the old hold: %+v, want the new hold of 20000 still held", res.Account)
	}
}

func TestOutboxPerOrg(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	for _, org := range []string{"org-a", "org-b"} {
		mustGrant(t, s, org, 1_000_000, 1, 0)
		s.Reserve(bg, org, "req-"+org, 10_000, time.Minute, billing.ReserveNew)
		if _, err := s.Settle(bg, org, "req-"+org, spendRecord(org, "req-"+org, 1_000)); err != nil {
			t.Fatal(err)
		}
	}
	for _, org := range []string{"org-a", "org-b"} {
		k := s.keys(org)
		entries := rdb.XRange(bg, k.outbox, "-", "+").Val()
		if len(entries) != 1 || entries[0].Values["org_id"] != org {
			t.Fatalf("%s's outbox = %v, want its own one record", org, entries)
		}
		for _, key := range []string{k.acct, k.holds, k.holdexp, k.outbox, k.settled("x")} {
			if want := "mc:{" + org + "}:"; !containsAfterPrefix(key, s.prefix, want) {
				t.Fatalf("key %q does not share the hash tag {%s}", key, org)
			}
		}
		if !rdb.SIsMember(bg, s.outboxOrgsKey(), org).Val() {
			t.Fatalf("%s missing from the discovery set", org)
		}
	}
}

func containsAfterPrefix(key, prefix, want string) bool {
	return len(key) >= len(prefix)+len(want) && key[:len(prefix)] == prefix && key[len(prefix):len(prefix)+len(want)] == want
}

func TestReserveNoAccountAndNeedsPush(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	res, err := s.Reserve(bg, "org-n", "r", 10, time.Minute, billing.ReserveNew)
	if err != nil || res.Status != billing.ReserveNoAccount {
		t.Fatalf("reserve without an account: %+v %v", res, err)
	}
	if rdb.Exists(bg, s.keys("org-n").acct).Val() != 0 {
		t.Fatal("RESERVE created an account hash")
	}
	if err := s.MarkNeedsPush(bg, "org-n"); err != nil || !rdb.SIsMember(bg, s.needsPushKey(), "org-n").Val() {
		t.Fatalf("needs_push: %v", err)
	}
}

func TestSettleOnLostAccountDoesNotRecreateIt(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-l", 100_000, 1, 0)
	s.Reserve(bg, "org-l", "r", 10_000, time.Minute, billing.ReserveNew)
	rdb.Del(bg, s.keys("org-l").acct)
	res, err := s.Settle(bg, "org-l", "r", spendRecord("org-l", "r", 9_000))
	if err != nil || !res.Applied || !res.AccountMissing {
		t.Fatalf("settle: %+v %v", res, err)
	}
	if rdb.Exists(bg, s.keys("org-l").acct).Val() != 0 {
		t.Fatal("SETTLE recreated a partial account; the next RESERVE would say exhausted, not no_account")
	}
	if rdb.XLen(bg, s.keys("org-l").outbox).Val() != 1 {
		t.Fatal("the spend record was not written")
	}
}

func TestOutboxPending(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	if n, err := s.OutboxPending(bg, "org-p"); err != nil || n != 0 {
		t.Fatalf("no outbox: %d %v", n, err)
	}
	mustGrant(t, s, "org-p", 1_000_000, 1, 0)
	for _, id := range []string{"a", "b", "c"} {
		s.Settle(bg, "org-p", id, spendRecord("org-p", id, 1))
	}
	if n, _ := s.OutboxPending(bg, "org-p"); n != 3 {
		t.Fatalf("before the drain's group exists: %d, want the stream length 3", n)
	}
	k := s.keys("org-p")
	rdb.XGroupCreate(bg, k.outbox, OutboxConsumerGroup, "0")
	got := rdb.XReadGroup(bg, &redis.XReadGroupArgs{Group: OutboxConsumerGroup, Consumer: "t", Streams: []string{k.outbox, ">"}, Count: 2}).Val()
	rdb.XAck(bg, k.outbox, OutboxConsumerGroup, got[0].Messages[0].ID)
	// One acked, one delivered and pending, one not yet delivered.
	if n, _ := s.OutboxPending(bg, "org-p"); n != 2 {
		t.Fatalf("with the group: %d, want pending 1 + lag 1", n)
	}
}

func TestManagedStoreUnavailable(t *testing.T) {
	s := NewManagedCreditStore(nil, "")
	if _, err := s.Reserve(bg, "org", "r", 1, time.Minute, billing.ReserveNew); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("nil client: %v, want ErrUnavailable", err)
	}
	open := &Client{breaker: newCircuitBreaker(1, time.Minute)}
	open.breaker.recordFailure()
	s = NewManagedCreditStore(open, "")
	if _, err := s.Settle(bg, "org", "r", billing.Settlement{}); !errors.Is(err, billing.ErrUnavailable) {
		t.Fatalf("open circuit: %v, want ErrUnavailable", err)
	}
	if s.Kind() != billing.KindRedis {
		t.Fatalf("kind = %q", s.Kind())
	}
}

// schemaFields is managed-settlement-v1's required field list.
func schemaFields(t *testing.T) []string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "managed-settlement-v1.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Required []string `json:"required"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	sort.Strings(schema.Required)
	return schema.Required
}

func fieldNames(v map[string]any) []string {
	var out []string
	for k := range v {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// A caller with no request context (a managed MCP tool call, a capability
// call) reserves, settles and voids by its own hold id and TTL, and writes
// the same outbox record shape as the credits plugin.
func TestStoreUsableOutsidePipeline(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	const org = "org-o"
	mustGrant(t, s, org, 1_000_000, 1, 0)
	k := s.keys(org)

	// The plugin path: a managed request reserved by the credits plugin and
	// settled with what the request was billed.
	plugin := creditsplugin.New(true, nil)
	plugin.SetOrgLedger(creditsplugin.NewOrgLedger(s), true)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-plugin"
	rc.Provider = "openrouter"
	rc.Request = &models.ChatCompletionRequest{Model: "vendor/model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["org_id"] = org
	if res := plugin.ProcessRequest(bg, rc); res.Error != nil {
		t.Fatalf("plugin reserve: %v", res.Error)
	}
	rc.Billing.BilledMicros, rc.Billing.UpstreamMicros = 2_100, 2_000
	plugin.ProcessResponse(bg, rc)

	// Outside the pipeline: a tool call keyed by its own id, with its own TTL.
	held, err := s.Reserve(bg, org, "call_123", 25_000, 30*time.Second, billing.ReserveNew)
	if err != nil || held.Status != billing.ReserveOK {
		t.Fatalf("reserve by hold id: %+v %v", held, err)
	}
	if ttl := rdb.ZScore(bg, k.holdexp, "call_123").Val(); ttl <= float64(time.Now().UnixMilli()) {
		t.Fatalf("hold expiry %v is not in the future", ttl)
	}
	tool := billing.Settlement{RequestID: "req-tool", OrgID: org, BilledMicros: 1_050, UpstreamMicros: 1_000,
		MarkupMicros: 50, MarkupBps: 500, HoldMicros: 25_000, Model: "fagi.search/web_search", Provider: "mcp",
		CostSource: "price_card", TsMs: time.Now().UnixMilli()}
	if res, err := s.Settle(bg, org, "call_123", tool); err != nil || !res.Applied {
		t.Fatalf("settle by hold id: %+v %v", res, err)
	}
	if res, _ := s.Settle(bg, org, "call_123", tool); res.Applied {
		t.Fatal("a second SETTLE of the same hold id wrote again")
	}
	s.Reserve(bg, org, "cap_1.1", 5_000, time.Minute, billing.ReserveNew)
	if res, err := s.Void(bg, org, "cap_1.1", billing.Settlement{RequestID: "cap_1", OrgID: org, Provider: "exa"}); err != nil || !res.Applied {
		t.Fatalf("void by hold id: %+v %v", res, err)
	}

	entries := rdb.XRange(bg, k.outbox, "-", "+").Val()
	if len(entries) != 3 {
		t.Fatalf("outbox has %d records, want plugin spend, tool spend and void", len(entries))
	}
	want := schemaFields(t)
	for i, e := range entries {
		if got := fieldNames(e.Values); fmt.Sprint(got) != fmt.Sprint(want) {
			t.Fatalf("record %d fields %v, want the managed-settlement-v1 fields %v", i, got, want)
		}
	}
	if entries[0].Values["request_id"] != "req-plugin" || entries[1].Values["provider"] != "mcp" || entries[2].Values["kind"] != "void" {
		t.Fatalf("records = %v", entries)
	}
	if a := mustAccount(t, s, org); a.SpentMicros != 2_100+1_050 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v", a)
	}

	// A hold whose TTL has passed is swept by the next RESERVE.
	s.Reserve(bg, org, "call_short", 100_000, 30*time.Millisecond, billing.ReserveNew)
	time.Sleep(80 * time.Millisecond)
	res, _ := s.Reserve(bg, org, "call_next", 1, time.Minute, billing.ReserveNew)
	if res.Account.HeldMicros != 1 || rdb.HExists(bg, k.holds, "call_short").Val() {
		t.Fatalf("the expired hold was not swept: %+v", res.Account)
	}
}
