package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The gateway and Django may deploy in either order (L4). The structural
// invariant is that the order never changes readiness or which keys load:
//   - every gateway that syncs keys is ready after its first key list,
//     whichever Django generation answered it, and no config seed is still
//     waiting (503 key_list_not_loaded) after that list;
//   - which keys authenticate is decided by the gateway's own rules applied
//     to the answer in front of it, never by what came before, so the
//     gateway-first and Django-first paths end in the same store;
//   - 733 ownership (a synced key needs user_id) is enforced, on the list
//     and on the additive POST /-/keys/sync push alike, once a key list
//     carrying the 733 snapshot has been applied, and from then on for good
//     (the 733 latch, 733 add.11): the "sequence" and "latch" groups below
//     drive one gateway through every ordering of control-plane events.
//
// This file uses only what both gateway generations have, so the same test
// runs in two trees. Here deploy_order_gateway_test.go builds a 733 store and
// the pre-733 rows skip. The pre-733 column runs against the pre-733 tree
// (bbaf95c973): its agentcc-gateway is extracted, this file is copied into
// internal/auth, and testdata/deploy_order_gateway_pre733_test.go is copied
// there as deploy_order_gateway_test.go; there the 733 rows skip.

const (
	deployOrderPre733 = "pre-733"
	deployOrder733    = "733"
)

// deployOrderRule is what one gateway, in one mode, facing one Django
// generation, must do. Readiness is not a field: it is the same for every row.
type deployOrderRule struct {
	gateway, django string
	controlPlane    bool
	// seedsWaitForList: before the first list a config seed answers 503
	// key_list_not_loaded (ConfigKeyPending) instead of serving.
	seedsWaitForList bool
	// ownerRequired: a listed or pushed key without user_id is refused.
	ownerRequired bool
	// collisionDropsBoth: a seed whose hash the control plane lists is
	// refused together with that key. Otherwise the config key wins.
	collisionDropsBoth bool
	// unlistedCollisionRefused: a seed whose hash only a deleted row holds is
	// refused. Only a 733 conflict answer (config_conflicts) reports it; a
	// pre-733 control plane has no such check, so that seed serves, as every
	// seed did before 733, until Django is upgraded (C4 revoked-hash restart).
	unlistedCollisionRefused bool
}

var deployOrderRules = []deployOrderRule{
	// A pre-733 gateway has no mode: main.go synced keys whenever a control
	// plane URL was set, and the store treated every key alike.
	{gateway: deployOrderPre733, django: deployOrderPre733, controlPlane: true},
	{gateway: deployOrderPre733, django: deployOrder733, controlPlane: true},
	{gateway: deployOrderPre733, django: deployOrderPre733},
	{gateway: deployOrderPre733, django: deployOrder733},
	// Gateway first: a 733 gateway behind a pre-733 Django.
	{gateway: deployOrder733, django: deployOrderPre733, controlPlane: true,
		seedsWaitForList: true, collisionDropsBoth: true},
	{gateway: deployOrder733, django: deployOrder733, controlPlane: true,
		seedsWaitForList: true, ownerRequired: true, collisionDropsBoth: true, unlistedCollisionRefused: true},
	// Standalone keeps its operator-owned config keys and never requires an owner.
	{gateway: deployOrder733, django: deployOrderPre733},
	{gateway: deployOrder733, django: deployOrder733},
}

const (
	deployOrderSeedsNone = "none"
	deployOrderSeedsSome = "some"
	// colliding: the harness seed's hash is a key the control plane lists.
	deployOrderSeedsColliding = "colliding"
	// colliding_unlisted: the harness seed's hash is held only by a row that
	// was deleted more than 7 days ago, so it is in no list and not retired.
	deployOrderSeedsCollidingUnlisted = "colliding_unlisted"
)

// deployOrderRow is one row of the control plane's key table.
type deployOrderRow struct {
	id, raw string
	owned   bool // metadata has user_id (a person's key), else org_id only
	active  bool // a pre-733 Django lists it; a 733 Django lists it only if owned
	stray   bool // probe: a 733 answer lists it although unowned, which a 733 Django never does
	recent  bool // updated within the 7-day window of a 733 Django's retired list
}

// deployOrderApp answers GET /agentcc/api-keys/bulk/ as each Django
// generation's APIKeyBulkView does, over one key table.
type deployOrderApp struct {
	internal, harness string // config seed raws; "" when absent
	rows              []deployOrderRow
}

func newDeployOrderApp(seedsKind string) *deployOrderApp {
	app := &deployOrderApp{}
	app.rows = []deployOrderRow{
		{id: "person", raw: generateKey(), owned: true, active: true},
		{id: "legacy", raw: generateKey(), active: true, recent: true},
		{id: "stray", raw: generateKey(), active: true, stray: true},
	}
	if seedsKind == deployOrderSeedsNone {
		return app
	}
	app.internal, app.harness = generateKey(), generateKey()
	switch seedsKind {
	case deployOrderSeedsColliding:
		app.rows = append(app.rows, deployOrderRow{id: "collision", raw: app.harness, owned: true, active: true})
	case deployOrderSeedsCollidingUnlisted:
		app.rows = append(app.rows, deployOrderRow{id: "deleted", raw: app.harness, owned: true})
	}
	return app
}

// config holds the deployment identities, as AGENTCC_INTERNAL_API_KEY and
// AGENTCC_HARNESS_API_KEY seed them.
func (app *deployOrderApp) config() config.AuthConfig {
	var cfg config.AuthConfig
	if app.internal != "" {
		cfg.Keys = []config.AuthKeyConfig{
			{Name: "internal", Key: app.internal, KeyType: "internal"},
			{Name: "harness", Key: app.harness, KeyType: "internal"},
		}
	}
	return cfg
}

func (app *deployOrderApp) seeds() []string {
	if app.internal == "" {
		return nil
	}
	return []string{app.internal, app.harness}
}

func (app *deployOrderApp) raw(id string) string {
	for _, row := range app.rows {
		if row.id == id {
			return row.raw
		}
	}
	return ""
}

// create stores a person's key and an org key made by a pre-733 Django,
// and returns them as Django pushes them (_restore_missing_keys). A 733
// Django would push only the person's key; the other probes the gateway.
func (app *deployOrderApp) create(batch string) []SyncedKey {
	person := deployOrderRow{id: batch + "-person", raw: generateKey(), owned: true, active: true}
	org := deployOrderRow{id: batch + "-legacy", raw: generateKey(), active: true, recent: true}
	app.rows = append(app.rows, person, org)
	return []SyncedKey{deployOrderSynced(person), deployOrderSynced(org)}
}

func deployOrderMetadata(row deployOrderRow) map[string]string {
	if row.owned {
		return map[string]string{"org_id": "org-deploy-order", "user_id": "user-deploy-order"}
	}
	return map[string]string{"org_id": "org-deploy-order"}
}

func deployOrderSynced(row deployOrderRow) SyncedKey {
	return SyncedKey{ID: row.id, Name: row.id, KeyHash: HashKey(row.raw), Metadata: deployOrderMetadata(row)}
}

func (app *deployOrderApp) listedBy(row deployOrderRow, django string) bool {
	if django == deployOrderPre733 {
		return row.active && !row.stray
	}
	return row.active && (row.owned || row.stray)
}

// answer is the bulk view's body. A pre-733 Django sends {status, result};
// a 733 Django always adds snapshot, retired and config_conflicts, the last
// even when the request named no seed hash.
func (app *deployOrderApp) answer(django, seedHeader string) map[string]interface{} {
	result := []interface{}{}
	retired := []interface{}{}
	stored := map[string]bool{}
	for _, row := range app.rows {
		stored[HashKey(row.raw)] = true
		if app.listedBy(row, django) {
			result = append(result, map[string]interface{}{
				"id": row.id, "name": row.id, "key_hash": HashKey(row.raw), "metadata": deployOrderMetadata(row),
			})
		} else if row.recent {
			retired = append(retired, map[string]interface{}{"id": row.id, "key_hash": HashKey(row.raw)})
		}
	}
	body := map[string]interface{}{"status": true, "result": result}
	if django == deployOrderPre733 {
		return body
	}
	conflicts := []string{}
	for _, hash := range strings.Split(seedHeader, ",") {
		if hash = strings.ToLower(strings.TrimSpace(hash)); stored[hash] {
			conflicts = append(conflicts, hash)
		}
	}
	sort.Strings(conflicts)
	body["snapshot"] = map[string]interface{}{"complete": true, "issued_ever": len(app.rows)}
	body["retired"] = retired
	body["config_conflicts"] = conflicts
	return body
}

func (app *deployOrderApp) pull(t *testing.T, ks *KeyStore, django string) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/api-keys/bulk/" {
			http.NotFound(w, r)
			return
		}
		json.NewEncoder(w).Encode(app.answer(django, r.Header.Get("X-Agentcc-Config-Key-Hashes")))
	}))
	defer srv.Close()
	if err := SyncKeysFromControlPlane(context.Background(), srv.URL, "", ks); err != nil {
		t.Fatalf("key sync from a %s control plane: %v", django, err)
	}
}

// deployOrderReady is server.go's /readyz key condition (readyHandler; the
// same condition in both generations): a gateway that syncs keys waits for
// its first list, or for the app's answer that it has no key table yet.
func deployOrderReady(ks *KeyStore, controlPlane bool) bool {
	return !controlPlane || ks.KeyListLoaded() || ks.AppHasNoKeyTable()
}

// allRaws lists every credential the cell can present, seeds included.
func (app *deployOrderApp) allRaws() []string {
	raws := app.seeds()
	for _, row := range app.rows {
		if row.raw != app.harness {
			raws = append(raws, row.raw)
		}
	}
	return raws
}

func deployOrderAuthenticated(ks *KeyStore, raws []string) map[string]bool {
	got := make(map[string]bool, len(raws))
	for _, raw := range raws {
		got[raw] = ks.Authenticate(raw) != nil
	}
	return got
}

func (app *deployOrderApp) name(raw string) string {
	switch raw {
	case app.internal:
		return "seed internal"
	case app.harness:
		return "seed harness"
	}
	for _, row := range app.rows {
		if row.raw == raw {
			return "key " + row.id
		}
	}
	return "unknown key"
}

func Test733DeployOrderNeverChangesReadinessOrKeys(t *testing.T) {
	seedKinds := []string{deployOrderSeedsNone, deployOrderSeedsSome, deployOrderSeedsColliding, deployOrderSeedsCollidingUnlisted}
	paths := []string{"list", "push"}
	modeName := map[bool]string{true: "control-plane", false: "standalone"}
	otherTree := func(gateway string) string {
		if gateway == deployOrderPre733 {
			return "the pre-733 gateway column runs against the pre-733 tree (bbaf95c973) with testdata/deploy_order_gateway_pre733_test.go as deploy_order_gateway_test.go"
		}
		return "the 733 gateway column runs in the 733 tree"
	}

	for _, rule := range deployOrderRules {
		// The table's own invariant: ownership exactly when a 733 gateway in
		// control-plane mode reads a 733 answer.
		if want := rule.gateway == deployOrder733 && rule.controlPlane && rule.django == deployOrder733; rule.ownerRequired != want {
			t.Fatalf("table row %+v: ownerRequired must be %v", rule, want)
		}
		for _, seedsKind := range seedKinds {
			for _, path := range paths {
				name := fmt.Sprintf("gateway=%s,django=%s,%s,seeds=%s,%s", rule.gateway, rule.django, modeName[rule.controlPlane], seedsKind, path)
				t.Run(name, func(t *testing.T) {
					if rule.gateway != deployOrderGateway {
						t.Skip(otherTree(rule.gateway))
					}
					app := newDeployOrderApp(seedsKind)
					ks := deployOrderStore(app.config(), rule.controlPlane)

					if got := deployOrderReady(ks, rule.controlPlane); got != !rule.controlPlane {
						t.Fatalf("ready before the first list = %v, want %v", got, !rule.controlPlane)
					}
					for _, seed := range app.seeds() {
						if got := deployOrderSeedPending(ks, seed); got != rule.seedsWaitForList {
							t.Errorf("%s before the first list: pending = %v, want %v", app.name(seed), got, rule.seedsWaitForList)
						}
						if got := ks.Authenticate(seed) != nil; got == rule.seedsWaitForList {
							t.Errorf("%s before the first list: authenticated = %v, want %v", app.name(seed), got, !rule.seedsWaitForList)
						}
					}
					if path == "push" {
						// No list has been read, so no 733 snapshot is present
						// and no owner is required yet: both keys load.
						if n := ks.LoadFromHashes(app.create("pushed-before-list")); n != 2 {
							t.Errorf("push before the first list loaded %d keys, want 2", n)
						}
						if got := deployOrderReady(ks, rule.controlPlane); got != !rule.controlPlane {
							t.Errorf("ready after a push, before any list = %v, want %v: a push is not a list", got, !rule.controlPlane)
						}
					}

					app.pull(t, ks, rule.django)
					if !ks.KeyListLoaded() || !deployOrderReady(ks, rule.controlPlane) {
						t.Errorf("not ready after the first list: a %s gateway (%s) behind a %s Django with seeds %s; /readyz stays 503",
							rule.gateway, modeName[rule.controlPlane], rule.django, seedsKind)
					}
					for _, seed := range app.seeds() {
						if deployOrderSeedPending(ks, seed) {
							t.Errorf("%s still answers 503 key_list_not_loaded after the first list", app.name(seed))
						}
					}
					if path == "push" {
						ks.LoadFromHashes(app.create("pushed-after-list"))
					}

					want := map[string]bool{
						app.raw("person"): true,
						app.raw("legacy"): rule.django == deployOrderPre733,
						// Only a 733 answer lists the stray probe.
						app.raw("stray"): rule.django == deployOrder733 && !rule.ownerRequired,
					}
					switch seedsKind {
					case deployOrderSeedsSome:
						want[app.internal], want[app.harness] = true, true
					case deployOrderSeedsColliding:
						want[app.internal], want[app.harness] = true, !rule.collisionDropsBoth
					case deployOrderSeedsCollidingUnlisted:
						want[app.internal], want[app.harness] = true, !rule.unlistedCollisionRefused
					}
					if path == "push" {
						// Pushed before the list: the list decides, like any listed key.
						want[app.raw("pushed-before-list-person")] = true
						want[app.raw("pushed-before-list-legacy")] = rule.django == deployOrderPre733
						// Pushed after the list: ownership follows that list's snapshot.
						want[app.raw("pushed-after-list-person")] = true
						want[app.raw("pushed-after-list-legacy")] = !rule.ownerRequired
					}
					got := deployOrderAuthenticated(ks, app.allRaws())
					for _, raw := range app.allRaws() {
						if got[raw] != want[raw] {
							t.Errorf("%s: authenticated = %v, want %v", app.name(raw), got[raw], want[raw])
						}
					}
				})
			}
		}
	}

	// Both orders end with a 733 gateway behind a 733 Django. Gateway first,
	// the 733 gateway read the pre-733 Django's lists (and pushes) before
	// Django was upgraded; Django first, the 733 gateway started last and
	// read only 733 answers. The two stores must agree on every credential.
	for _, controlPlane := range []bool{true, false} {
		for _, seedsKind := range seedKinds {
			for _, path := range paths {
				t.Run(fmt.Sprintf("order,%s,seeds=%s,%s", modeName[controlPlane], seedsKind, path), func(t *testing.T) {
					if deployOrderGateway != deployOrder733 {
						t.Skip(otherTree(deployOrder733))
					}
					app := newDeployOrderApp(seedsKind)
					gatewayFirst := deployOrderStore(app.config(), controlPlane)
					if path == "push" {
						gatewayFirst.LoadFromHashes(app.create("pushed-before-list"))
					}
					app.pull(t, gatewayFirst, deployOrderPre733)
					if path == "push" {
						gatewayFirst.LoadFromHashes(app.create("pushed-after-list"))
					}
					app.pull(t, gatewayFirst, deployOrder733)

					djangoFirst := deployOrderStore(app.config(), controlPlane)
					app.pull(t, djangoFirst, deployOrder733)

					if a, b := deployOrderReady(gatewayFirst, controlPlane), deployOrderReady(djangoFirst, controlPlane); a != b || !a {
						t.Errorf("ready: gateway first %v, Django first %v; want both true", a, b)
					}
					if a, b := gatewayFirst.Count(), djangoFirst.Count(); a != b {
						t.Errorf("stored keys: gateway first %d, Django first %d", a, b)
					}
					first := deployOrderAuthenticated(gatewayFirst, app.allRaws())
					last := deployOrderAuthenticated(djangoFirst, app.allRaws())
					for _, raw := range app.allRaws() {
						if first[raw] != last[raw] {
							t.Errorf("%s: authenticated gateway first = %v, Django first = %v", app.name(raw), first[raw], last[raw])
						}
						if a, b := deployOrderSeedPending(gatewayFirst, raw), deployOrderSeedPending(djangoFirst, raw); a || b {
							t.Errorf("%s: pending gateway first = %v, Django first = %v", app.name(raw), a, b)
						}
					}
				})
			}
		}
	}

	// The sequence dimension: every ordering of up to three control-plane
	// events, and after each event the keys that authenticate are exactly
	// what the latest applied answer permits under the 733 latch.
	t.Run("sequence", func(t *testing.T) {
		if deployOrderGateway != deployOrder733 {
			t.Skip("the sequence dimension is the 733 gateway's latch (733 add.11); a pre-733 gateway has no ownership rule to latch")
		}
		seqQuietLogs(t)
		sequences := seqSequences(seqSteps, 3)
		cells := 0
		for _, controlPlane := range []bool{true, false} {
			for _, seeds := range []bool{false, true} {
				for _, stored := range []bool{true, false} {
					for _, steps := range sequences {
						cells++
						t.Run(seqCellName(controlPlane, seeds, stored, steps), func(t *testing.T) {
							newSeqWorld(controlPlane, seeds, stored).run(t, steps)
						})
					}
				}
			}
		}
		// A generator bug must not shrink the matrix silently.
		if want := 2 * 2 * 2 * (6 + 6*6 + 6*6*6); cells != want {
			t.Fatalf("sequence cells = %d, want %d", cells, want)
		}
	})

	// Named cases of the latch, each with its expectation written out by
	// hand rather than taken from the sequence model, so the model is checked
	// too. Control-plane mode with the hosted seeds.
	t.Run("latch", func(t *testing.T) {
		if deployOrderGateway != deployOrder733 {
			t.Skip("the 733 latch (733 add.11) is the 733 gateway's; a pre-733 gateway has no ownership rule to latch")
		}
		seqQuietLogs(t)
		for _, tc := range []struct {
			name   string
			steps  []string
			stored bool
			want   map[string]bool // after the last event
		}{
			// 733 add.11 (b): an unowned key pushed before the first list is
			// dropped by that list when it is a 733 one, whatever its kind.
			{"unowned push before the first list is dropped by a 733 list",
				[]string{seqPushUnowned, seq733}, true,
				map[string]bool{"person": true, "legacy": false, "push1-unowned": false}},
			{"unowned push before the first list is dropped by a fresh empty 733 answer",
				[]string{seqPushUnowned, seq733FreshEmpty}, true,
				map[string]bool{"person": false, "legacy": false, "push1-unowned": false}},
			{"unowned push before the first list is dropped by an incomplete empty 733 answer",
				[]string{seqPushUnowned, seq733Incomplete}, true,
				map[string]bool{"person": false, "legacy": false, "push1-unowned": false}},
			// A pre-733 first list permits the unowned key it carries.
			{"unowned push before a pre-733 first list that carries it is kept",
				[]string{seqPushUnowned, seqPre733}, true,
				map[string]bool{"person": true, "legacy": true, "push1-unowned": true}},
			{"unowned push before a pre-733 first list that omits it is dropped",
				[]string{seqPushUnowned, seqPre733}, false,
				map[string]bool{"person": true, "legacy": true, "push1-unowned": false}},
			// A non-authoritative 733 answer keeps the keys held,
			// but not the unowned ones.
			{"a fresh empty 733 answer drops the unowned keys a pre-733 list loaded",
				[]string{seqPre733, seq733FreshEmpty}, true,
				map[string]bool{"person": true, "legacy": false}},
			{"an incomplete empty 733 answer drops the unowned keys a pre-733 list loaded",
				[]string{seqPre733, seq733Incomplete}, true,
				map[string]bool{"person": true, "legacy": false}},
			{"a fresh empty 733 answer keeps an owned push",
				[]string{seqPushOwned, seq733FreshEmpty}, true,
				map[string]bool{"person": false, "legacy": false, "push1-owned": true}},
			// 733 add.11 (a): the latch never reverts, on the list or the push.
			{"a pre-733 list after a fresh empty 733 answer loads no unowned key",
				[]string{seq733FreshEmpty, seqPre733}, true,
				map[string]bool{"person": true, "legacy": false}},
			{"a pre-733 list after an incomplete empty 733 answer loads no unowned key",
				[]string{seq733Incomplete, seqPre733}, true,
				map[string]bool{"person": true, "legacy": false}},
			{"an unowned push after a 733 list and a pre-733 list is refused",
				[]string{seq733, seqPre733, seqPushUnowned}, true,
				map[string]bool{"person": true, "legacy": false, "push1-unowned": false}},
			{"a pre-733 list carrying an unowned key pushed after the latch does not load it",
				[]string{seq733, seqPushUnowned, seqPre733}, true,
				map[string]bool{"person": true, "legacy": false, "push1-unowned": false}},
			{"an owned push after the latch and a pre-733 list is kept",
				[]string{seq733FreshEmpty, seqPre733, seqPushOwned}, true,
				map[string]bool{"person": true, "legacy": false, "push1-owned": true}},
		} {
			t.Run(tc.name, func(t *testing.T) {
				w := newSeqWorld(true, true, tc.stored)
				ks := w.run(t, tc.steps)
				if !deployOrderReady(ks, true) {
					t.Error("not ready after the events")
				}
				for _, seed := range w.seeds() {
					if ks.Authenticate(seed) == nil {
						t.Errorf("%s does not authenticate after the events", w.name(seed))
					}
				}
				for name, want := range tc.want {
					key := w.key(name)
					if key == nil {
						t.Fatalf("no key %s in this case", name)
					}
					if got := ks.Authenticate(key.raw) != nil; got != want {
						t.Errorf("key %s: authenticated = %v, want %v", name, got, want)
					}
					if w.held[key] != want {
						t.Errorf("key %s: the sequence model says %v, the hand-written case %v", name, w.held[key], want)
					}
				}
			})
		}
	})
}

// The key sync grace on a pushed key (kept, by design).
// LoadFromHashes gives a key POST /-/keys/sync pushes the window Create gives
// (keystore.go SetSyncGrace; config.DefaultKeySyncGrace, 30 s), because a list
// Django answers just after the push may not carry the key as ACTIVE yet. A
// list that omits the key keeps it while the key is younger than 30 s and
// refuses it once it is 30 s old. A negative fact ends the window at once:
// Revoke, or a retired row in the list. Control-plane mode with the hosted
// seeds, after a 733 list, with a person-owned key that no list carries.
func Test733PushedKeyGraceIs30Seconds(t *testing.T) {
	if deployOrderGateway != deployOrder733 {
		t.Skip("the pushed-key grace is pinned in the 733 tree")
	}
	if config.DefaultKeySyncGrace != 30*time.Second {
		t.Fatalf("config.DefaultKeySyncGrace = %v; this test pins 30 s", config.DefaultKeySyncGrace)
	}
	seqQuietLogs(t)
	for _, end := range []string{"age", "revoke", "retired"} {
		t.Run(end, func(t *testing.T) {
			w := newSeqWorld(true, true, false)
			ks := deployOrderStore(w.config(), true)
			clock := &fakeClock{t: time.Date(2026, 11, 11, 0, 0, 0, 0, time.UTC)}
			ks.now = clock.Now
			cp := newSeqControlPlane(t)
			cp.pull(t, ks, w.answer(seq733))
			pushed := w.expect(seqPushOwned) // pushes=unstored: no list carries it
			if n := ks.LoadFromHashes([]SyncedKey{{ID: pushed.name, Name: pushed.name, KeyHash: HashKey(pushed.raw), Metadata: seqMetadata(pushed)}}); n != 1 {
				t.Fatalf("the push loaded %d keys, want 1", n)
			}
			if ks.Authenticate(pushed.raw) == nil {
				t.Fatal("a just-pushed person-owned key does not authenticate")
			}
			switch end {
			case "age":
				clock.Advance(29 * time.Second)
				cp.pull(t, ks, w.answer(seq733))
				if ks.Authenticate(pushed.raw) == nil {
					t.Fatal("a list fetched 29 s after the push refused the key it omits; the 30 s grace keeps it")
				}
				clock.Advance(time.Second)
				cp.pull(t, ks, w.answer(seq733))
				if ks.Authenticate(pushed.raw) != nil {
					t.Fatal("a list fetched 30 s after the push still lets the key it omits authenticate")
				}
			case "revoke":
				if !ks.Revoke(pushed.name) {
					t.Fatal("Revoke did not find the pushed key")
				}
				if ks.Authenticate(pushed.raw) != nil {
					t.Fatal("a revoked key authenticates inside the grace")
				}
				clock.Advance(time.Second)
				cp.pull(t, ks, w.answer(seq733))
				if ks.Authenticate(pushed.raw) != nil {
					t.Fatal("a list fetched 1 s after the push brought a revoked key back")
				}
			case "retired":
				clock.Advance(time.Second)
				answer := w.answer(seq733)
				retired, _ := answer["retired"].([]interface{})
				answer["retired"] = append(retired, map[string]interface{}{"id": pushed.name, "key_hash": HashKey(pushed.raw)})
				cp.pull(t, ks, answer)
				if ks.Authenticate(pushed.raw) != nil {
					t.Fatal("a list fetched 1 s after the push retired the key, and it still authenticates")
				}
			}
			if person := w.key("person"); ks.Authenticate(person.raw) == nil {
				t.Error("the listed person key stopped authenticating")
			}
		})
	}
}

// The sequence dimension's events (733 add.11). Every list event goes
// through SyncKeysFromControlPlane, every push through LoadFromHashes, as
// POST /-/keys/sync does.
const (
	// A pre-733 Django's list: every stored row, no 733 field.
	seqPre733 = "pre733"
	// POST /-/keys/sync of a new key with org_id only, or with user_id too.
	seqPushUnowned = "push-unowned"
	seqPushOwned   = "push-owned"
	// A 733 Django's list: its person-owned rows, snapshot{complete,
	// issued_ever>0} (authoritative), every unowned stored row retired,
	// config_conflicts.
	seq733 = "733"
	// 733 answers with an empty result, neither authoritative:
	// snapshot{complete:true, issued_ever:0} (a fresh install), and
	// snapshot{complete:false, issued_ever>0}.
	seq733FreshEmpty = "733-fresh-empty"
	seq733Incomplete = "733-incomplete-empty"
)

var seqSteps = []string{seqPre733, seqPushUnowned, seqPushOwned, seq733, seq733FreshEmpty, seq733Incomplete}

// seqSequences returns every sequence of 1 to maxLen steps, repeats allowed.
func seqSequences(steps []string, maxLen int) [][]string {
	var out [][]string
	var grow func(prefix []string)
	grow = func(prefix []string) {
		if len(prefix) > 0 {
			out = append(out, append([]string(nil), prefix...))
		}
		if len(prefix) == maxLen {
			return
		}
		for _, step := range steps {
			grow(append(append([]string(nil), prefix...), step))
		}
	}
	grow(nil)
	return out
}

func seqCellName(controlPlane, seeds, stored bool, steps []string) string {
	mode, seedsKind, pushes := "standalone", deployOrderSeedsNone, "unstored"
	if controlPlane {
		mode = "control-plane"
	}
	if seeds {
		seedsKind = deployOrderSeedsSome
	}
	if stored {
		pushes = "stored"
	}
	return fmt.Sprintf("%s,seeds=%s,pushes=%s,%s", mode, seedsKind, pushes, strings.Join(steps, ">"))
}

// seqQuietLogs keeps the matrix's verbose output to its results: thousands of
// syncs would otherwise log a line each.
func seqQuietLogs(t *testing.T) {
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(io.Discard, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
}

type seqKey struct {
	name, raw string
	owned     bool // metadata has user_id, else org_id only
	stored    bool // Django's table holds it, so later lists carry it
}

// seqWorld is one gateway's control plane and the sequence model: what the
// latest applied answer permits, computed from the events alone, never from
// the store. Its modelling choices:
//   - pushes=stored: Django stores what it pushes, so a later list carries a
//     pushed key as it carries any row; pushes=unstored: no list carries it.
//   - Every list event is fetched more than config.DefaultKeySyncGrace (30 s)
//     after the event before it (run moves the store's clock), so the key
//     sync grace (keystore.go SetSyncGrace) has run out for every key pushed
//     earlier, and a list that omits a pushed key refuses it from then on.
//     The window itself is pinned by Test733PushedKeyGraceIs30Seconds.
//   - A 733 list retires every unowned stored row (all of them are recent),
//     so a later list that carries one loads it revoked, in standalone mode
//     too, where ownership never applies.
//   - The empty 733 answers carry no retired row. Being non-authoritative,
//     they keep the keys held, minus every key without user_id once the
//     gateway, in control-plane mode, has latched 733 semantics.
//   - Every 733 answer carries config_conflicts (empty: no seed collides), so
//     the first list of any kind releases the seeds and readiness.
type seqWorld struct {
	controlPlane, storePushes bool
	internal, harness         string // config seed raws; "" when absent
	keys                      []*seqKey
	pushes                    int

	latched, loaded bool
	held, blocked   map[*seqKey]bool
}

func newSeqWorld(controlPlane, seeds, storePushes bool) *seqWorld {
	w := &seqWorld{
		controlPlane: controlPlane, storePushes: storePushes,
		held: map[*seqKey]bool{}, blocked: map[*seqKey]bool{},
		keys: []*seqKey{
			{name: "person", raw: generateKey(), owned: true, stored: true},
			{name: "legacy", raw: generateKey(), stored: true},
		},
	}
	if seeds {
		w.internal, w.harness = generateKey(), generateKey()
	}
	return w
}

func (w *seqWorld) config() config.AuthConfig {
	var cfg config.AuthConfig
	if w.internal != "" {
		cfg.Keys = []config.AuthKeyConfig{
			{Name: "internal", Key: w.internal, KeyType: "internal"},
			{Name: "harness", Key: w.harness, KeyType: "internal"},
		}
	}
	return cfg
}

func (w *seqWorld) seeds() []string {
	if w.internal == "" {
		return nil
	}
	return []string{w.internal, w.harness}
}

func (w *seqWorld) key(name string) *seqKey {
	for _, k := range w.keys {
		if k.name == name {
			return k
		}
	}
	return nil
}

func (w *seqWorld) name(raw string) string {
	switch raw {
	case w.internal:
		return "seed internal"
	case w.harness:
		return "seed harness"
	}
	return "unknown key"
}

func seqMetadata(k *seqKey) map[string]string {
	if k.owned {
		return map[string]string{"org_id": "org-sequence", "user_id": "user-sequence"}
	}
	return map[string]string{"org_id": "org-sequence"}
}

// answer is the bulk view's body for a list event, over the stored rows.
func (w *seqWorld) answer(step string) map[string]interface{} {
	result, retired := []interface{}{}, []interface{}{}
	stored := 0
	for _, k := range w.keys {
		if !k.stored {
			continue
		}
		stored++
		row := map[string]interface{}{"id": k.name, "name": k.name, "key_hash": HashKey(k.raw), "metadata": seqMetadata(k)}
		switch {
		case step == seqPre733, step == seq733 && k.owned:
			result = append(result, row)
		case step == seq733:
			retired = append(retired, map[string]interface{}{"id": k.name, "key_hash": HashKey(k.raw)})
		}
	}
	body := map[string]interface{}{"status": true, "result": result}
	switch step {
	case seqPre733:
		return body
	case seq733:
		body["snapshot"] = map[string]interface{}{"complete": true, "issued_ever": stored}
	case seq733FreshEmpty:
		body["snapshot"] = map[string]interface{}{"complete": true, "issued_ever": 0}
	case seq733Incomplete:
		body["snapshot"] = map[string]interface{}{"complete": false, "issued_ever": stored}
	}
	body["retired"] = retired
	body["config_conflicts"] = []string{}
	return body
}

// permitted is whether, under the latch, a synced key may authenticate at all.
func (w *seqWorld) permitted(k *seqKey) bool {
	return k.owned || !w.controlPlane || !w.latched
}

// expect advances the model by one event; a push returns the key it makes.
func (w *seqWorld) expect(step string) *seqKey {
	switch step {
	case seqPushUnowned, seqPushOwned:
		w.pushes++
		k := &seqKey{name: fmt.Sprintf("push%d-%s", w.pushes, strings.TrimPrefix(step, "push-")),
			raw: generateKey(), owned: step == seqPushOwned, stored: w.storePushes}
		w.keys = append(w.keys, k)
		w.held[k] = w.permitted(k)
		return k
	case seqPre733, seq733:
		// A nonempty list replaces every synced key; a retired row is
		// blocked first, and a blocked key that is listed loads revoked.
		if step == seq733 {
			w.latched = true
			for _, k := range w.keys {
				if k.stored && !k.owned {
					w.blocked[k] = true
				}
			}
		}
		w.loaded = true
		w.held = map[*seqKey]bool{}
		for _, k := range w.keys {
			listed := k.stored && (step == seqPre733 || k.owned)
			w.held[k] = listed && !w.blocked[k] && w.permitted(k)
		}
	case seq733FreshEmpty, seq733Incomplete:
		w.latched, w.loaded = true, true
		for k := range w.held {
			w.held[k] = w.held[k] && w.permitted(k)
		}
	}
	return nil
}

// run drives a new gateway through steps, checking it against the model
// before the first event and after each one, and returns its store.
func (w *seqWorld) run(t *testing.T, steps []string) *KeyStore {
	t.Helper()
	ks := deployOrderStore(w.config(), w.controlPlane)
	// The store reads the test's clock (KeyStore.now), so each list below is
	// fetched past the grace of every key pushed before it.
	clock := &fakeClock{t: time.Date(2026, 11, 11, 0, 0, 0, 0, time.UTC)}
	ks.now = clock.Now
	cp := newSeqControlPlane(t)
	w.check(t, ks, "before any event")
	for i, step := range steps {
		if step == seqPushUnowned || step == seqPushOwned {
			k := w.expect(step)
			ks.LoadFromHashes([]SyncedKey{{ID: k.name, Name: k.name, KeyHash: HashKey(k.raw), Metadata: seqMetadata(k)}})
		} else {
			clock.Advance(config.DefaultKeySyncGrace + time.Second)
			cp.pull(t, ks, w.answer(step))
			w.expect(step)
		}
		w.check(t, ks, fmt.Sprintf("after event %d (%s)", i+1, step))
	}
	return ks
}

func (w *seqWorld) check(t *testing.T, ks *KeyStore, when string) {
	t.Helper()
	if got, want := deployOrderReady(ks, w.controlPlane), !w.controlPlane || w.loaded; got != want {
		t.Errorf("%s: ready = %v, want %v", when, got, want)
	}
	pending := w.controlPlane && !w.loaded
	for _, seed := range w.seeds() {
		if got := deployOrderSeedPending(ks, seed); got != pending {
			t.Errorf("%s: %s pending = %v, want %v", when, w.name(seed), got, pending)
		}
		if got := ks.Authenticate(seed) != nil; got == pending {
			t.Errorf("%s: %s authenticated = %v, want %v", when, w.name(seed), got, !pending)
		}
	}
	for _, k := range w.keys {
		owner := "unowned"
		if k.owned {
			owner = "person-owned"
		}
		if got := ks.Authenticate(k.raw) != nil; got != w.held[k] {
			t.Errorf("%s: key %s (%s): authenticated = %v, want %v (latched=%v)", when, k.name, owner, got, w.held[k], w.latched)
		}
	}
}

// seqControlPlane answers every key sync with the body its last pull set.
type seqControlPlane struct {
	url  string
	mu   sync.Mutex
	body map[string]interface{}
}

func newSeqControlPlane(t *testing.T) *seqControlPlane {
	cp := &seqControlPlane{}
	srv := httptest.NewServer(http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/api-keys/bulk/" {
			http.NotFound(rw, r)
			return
		}
		cp.mu.Lock()
		body := cp.body
		cp.mu.Unlock()
		json.NewEncoder(rw).Encode(body)
	}))
	t.Cleanup(srv.Close)
	cp.url = srv.URL
	return cp
}

func (cp *seqControlPlane) pull(t *testing.T, ks *KeyStore, body map[string]interface{}) {
	t.Helper()
	cp.mu.Lock()
	cp.body = body
	cp.mu.Unlock()
	if err := SyncKeysFromControlPlane(context.Background(), cp.url, "", ks); err != nil {
		t.Fatalf("key sync: %v", err)
	}
}
