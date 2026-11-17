package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// This group uses the existing gateway and isolated Redis fixture, not the
// gateway fixture's default memory ledger. TEST_REDIS_ADDR must name a dedicated
// test Redis; TEST_REDIS_DB defaults to 0. A skipped Redis fixture is not proof.
// Only chat completions, unexpired pre-existing holds and undrained outboxes are
// covered here. No consumer group, purchase, production wallet or drain runs.
func TestB5MoneyRefusalNoCharge(t *testing.T) {
	for _, tc := range []struct {
		name       string
		model      string
		creditsOff bool
		balance    string
		status     int
		code       string
		admitted   bool
	}{
		// model_answers.go distinguishes disabled credits from an unavailable
		// managed price; credits/refusals.go names exhausted/insufficient credit.
		{name: "credits_off", model: "acme/request", creditsOff: true, status: http.StatusForbidden, code: "future_agi_credits_off"},
		{name: "credits_exhausted", model: "acme/request", balance: "exhausted", status: http.StatusPaymentRequired, code: "managed_credits_exhausted"},
		{name: "credits_insufficient", model: "acme/request", balance: "insufficient", status: http.StatusPaymentRequired, code: "managed_credits_insufficient"},
		{name: "unpriced_model", model: "acme/unpriced", status: http.StatusForbidden, code: "permission_denied"},
		{name: "unsupported_price_dimension", model: "acme/unsupported", status: http.StatusForbidden, code: "permission_denied"},
		{name: "unlisted_model", model: "acme/absent", status: http.StatusNotFound, code: "model_not_found"},
		{name: "funded_admission_control", model: "acme/request", status: http.StatusOK, admitted: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			prefix := fmt.Sprintf("test-b5-money-refusal-%d-%s:", time.Now().UnixNano(), tc.name)
			client := r7AgentRedis(t, prefix)
			org := managedOptIn()
			if tc.creditsOff {
				org.ManagedModels.Enabled = false
			}
			g := newManagedTestGateway(t, org)
			installBillabilityModels(billabilityModels(t))(t, g)
			ledger := creditsplugin.NewOrgLedger(redisstate.NewManagedCreditStore(client, prefix))
			g.ledger = ledger
			g.credits.SetOrgLedger(ledger, true)
			g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
			if ledger.Kind() != billing.KindRedis {
				t.Fatal("this evidence requires the Redis ledger")
			}

			// Different nonzero grants, versions, spends, pending records and
			// outstanding holds make account swaps and cancelling mutations
			// visible. B is unrelated to the HTTP request's authenticated org.
			a := b5MoneyRefusalSeed(t, ledger, "org-m", 1_000_000, 7, 17_000, 701, 307)
			b5MoneyRefusalSeed(t, ledger, "org-b", 2_000_000, 11, 23_000, 997, 509)
			if tc.balance != "" {
				granted := a.SpentMicros
				if tc.balance == "insufficient" {
					// Positive available credit, but even releasing the old hold
					// cannot fund acme/request's fixed 42,000-micro reservation.
					granted += a.HeldMicros + 1_000
				}
				// A newer grant may be below an outstanding hold. Exhaustion is
				// granted-spent == 0, not merely an in-flight reservation cap.
				res, err := ledger.Grant(context.Background(), "org-m", billing.GrantPush{
					GrantedTotalMicros: granted, Version: a.GrantedVersion + 1,
				})
				if err != nil || !res.Applied || res.Account.SpentMicros != a.SpentMicros || res.Account.HeldMicros != a.HeldMicros {
					t.Fatalf("setting refusal balance: result=%+v err=%v", res, err)
				}
				if tc.balance == "exhausted" && res.Account.GrantedMicros-res.Account.SpentMicros != 0 {
					t.Fatalf("not exhausted: %+v", res.Account)
				}
				if tc.balance == "insufficient" && res.Account.AvailableMicros() != 1_000 {
					t.Fatalf("not the positive insufficient balance: %+v", res.Account)
				}
			}

			ts := httptest.NewServer(g.srv.httpServer.Handler)
			defer ts.Close()
			httpClient := ts.Client()
			httpClient.Timeout = 5 * time.Second
			req, err := http.NewRequest(http.MethodPost, ts.URL+"/v1/chat/completions", strings.NewReader(chatBody(tc.model)))
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("Authorization", "Bearer "+managedOrgKey)
			req.Header.Set("Content-Type", "application/json")

			before := b5MoneyRefusalSnapshot(t, client, ledger, prefix)
			managedBefore, operatorBefore := g.managed.calls(), g.operator.calls()
			if managedBefore != 0 || operatorBefore != 0 {
				t.Fatal("the fixture dispatched before its one HTTP request")
			}
			resp, err := httpClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			body, readErr := io.ReadAll(resp.Body)
			closeErr := resp.Body.Close()
			// Snapshot before checking the response so a wrong status/code
			// cannot conceal a credit, hold or outbox mutation.
			after := b5MoneyRefusalSnapshot(t, client, ledger, prefix)
			managedCalls, operatorCalls := g.managed.calls()-managedBefore, g.operator.calls()-operatorBefore

			if !tc.admitted {
				if !reflect.DeepEqual(before, after) {
					t.Errorf("one refused request changed durable state:\nbefore=%+v\nafter=%+v", before, after)
				}
				if managedCalls != 0 || operatorCalls != 0 {
					t.Errorf("refusal dispatched inference: managed=%d operator=%d; want 0,0", managedCalls, operatorCalls)
				}
			} else {
				if managedCalls != 1 || operatorCalls != 0 || g.managed.lastModel() != tc.model {
					t.Errorf("positive control: managed=%d operator=%d model=%q", managedCalls, operatorCalls, g.managed.lastModel())
				}
				if resp.Header.Get("x-agentcc-billing-mode") != "managed" {
					t.Errorf("positive control billing mode=%q", resp.Header.Get("x-agentcc-billing-mode"))
				}
				wantA := before.accounts["org-m"]
				// The local upstream reports 7,000 micros, plus 5% markup.
				wantA.SpentMicros += 7_350
				if after.accounts["org-m"] != wantA || after.pending["org-m"] != before.pending["org-m"]+1 {
					t.Errorf("positive control did not settle exactly once: account=%+v pending=%d; want %+v pending=%d",
						after.accounts["org-m"], after.pending["org-m"], wantA, before.pending["org-m"]+1)
				}
				if before.accounts["org-b"] != after.accounts["org-b"] || before.pending["org-b"] != after.pending["org-b"] ||
					!reflect.DeepEqual(b5MoneyRefusalOrgKeys(before.keys, prefix, "org-b"), b5MoneyRefusalOrgKeys(after.keys, prefix, "org-b")) {
					t.Error("positive control changed unrelated account B, its holds or its outbox")
				}
			}
			if readErr != nil || closeErr != nil {
				t.Fatalf("reading response: read=%v close=%v", readErr, closeErr)
			}
			var envelope struct {
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if err := json.Unmarshal(body, &envelope); err != nil {
				t.Fatalf("decoding HTTP %d response %q: %v", resp.StatusCode, body, err)
			}
			if resp.StatusCode != tc.status || envelope.Error.Code != tc.code {
				t.Errorf("HTTP %d code=%q body=%s; want HTTP %d code=%q", resp.StatusCode, envelope.Error.Code, body, tc.status, tc.code)
			}
		})
	}
}

// Seed only through the durable ledger APIs. The long-lived hold cannot expire
// during a request and trigger the reserve script's intentional expiry sweep.
func b5MoneyRefusalSeed(t *testing.T, ledger *creditsplugin.OrgLedger, org string, granted, version, floor, spend, held int64) billing.Account {
	t.Helper()
	ctx := context.Background()
	grant, err := ledger.Grant(ctx, org, billing.GrantPush{GrantedTotalMicros: granted, Version: version, SettledSpendFloorMicros: floor})
	if err != nil || !grant.Applied {
		t.Fatalf("seed %s grant: result=%+v err=%v", org, grant, err)
	}
	reserve, err := ledger.Reserve(ctx, org, "b5-seed-settled", spend, time.Hour, billing.ReserveNew)
	if err != nil || reserve.Status != billing.ReserveOK {
		t.Fatalf("seed %s settled hold: result=%+v err=%v", org, reserve, err)
	}
	settled, err := ledger.Settle(ctx, org, "b5-seed-settled", billing.Settlement{BilledMicros: spend, UpstreamMicros: spend})
	if err != nil || !settled.Applied || settled.AccountMissing {
		t.Fatalf("seed %s settlement: result=%+v err=%v", org, settled, err)
	}
	reserve, err = ledger.Reserve(ctx, org, "b5-seed-outstanding", held, time.Hour, billing.ReserveNew)
	if err != nil || reserve.Status != billing.ReserveOK {
		t.Fatalf("seed %s outstanding hold: result=%+v err=%v", org, reserve, err)
	}
	want := billing.Account{Exists: true, GrantedMicros: granted, GrantedVersion: version, SpentMicros: floor + spend, HeldMicros: held}
	if reserve.Account != want {
		t.Fatalf("seed %s account=%+v; want %+v", org, reserve.Account, want)
	}
	pending, err := ledger.OutboxPending(ctx, org)
	if err != nil || pending != 1 {
		t.Fatalf("seed %s pending=%d err=%v; want one undrained record", org, pending, err)
	}
	return reserve.Account
}

type b5MoneyRefusalState struct {
	accounts map[string]billing.Account
	pending  map[string]int64
	keys     map[string]any
}

// Snapshot every key in this test's prefix as logical values, including the
// full account hashes, holds, expiry scores, settlement markers, outbox entries
// with IDs and fields, and cross-org discovery hints. Counts or net balances
// alone could hide a replacement record or a mutation cancelled in account B.
func b5MoneyRefusalSnapshot(t *testing.T, client *redisstate.Client, ledger *creditsplugin.OrgLedger, prefix string) b5MoneyRefusalState {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	state := b5MoneyRefusalState{
		accounts: make(map[string]billing.Account),
		pending:  make(map[string]int64),
		keys:     make(map[string]any),
	}
	for _, org := range []string{"org-m", "org-b"} {
		account, err := ledger.Account(ctx, org)
		if err != nil {
			t.Fatalf("snapshot %s account: %v", org, err)
		}
		pending, err := ledger.OutboxPending(ctx, org)
		if err != nil {
			t.Fatalf("snapshot %s pending: %v", org, err)
		}
		state.accounts[org], state.pending[org] = account, pending
	}
	rdb := client.Redis()
	iter := rdb.Scan(ctx, 0, prefix+"*", 100).Iterator()
	for iter.Next(ctx) {
		key := iter.Val()
		kind, err := rdb.Type(ctx, key).Result()
		if err != nil {
			t.Fatalf("snapshot type %s: %v", key, err)
		}
		var value any
		switch kind {
		case "hash":
			value, err = rdb.HGetAll(ctx, key).Result()
		case "zset":
			value, err = rdb.ZRangeWithScores(ctx, key, 0, -1).Result()
		case "stream":
			value, err = rdb.XRange(ctx, key, "-", "+").Result()
		case "string":
			value, err = rdb.Get(ctx, key).Result()
		case "set":
			var members []string
			members, err = rdb.SMembers(ctx, key).Result()
			sort.Strings(members)
			value = members
		default:
			t.Fatalf("snapshot unexpected Redis type %q for %s", kind, key)
		}
		if err != nil {
			t.Fatalf("snapshot %s: %v", key, err)
		}
		state.keys[key] = value
	}
	if err := iter.Err(); err != nil {
		t.Fatalf("snapshot prefix scan: %v", err)
	}
	return state
}

func b5MoneyRefusalOrgKeys(keys map[string]any, prefix, org string) map[string]any {
	result := make(map[string]any)
	for key, value := range keys {
		if strings.HasPrefix(key, prefix+"mc:{"+org+"}:") {
			result[key] = value
		}
	}
	return result
}
