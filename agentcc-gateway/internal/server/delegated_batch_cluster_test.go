package server

// A cluster whose nodes have Redis serves the delegated batch routes: the
// replica that took a batch runs it, and any replica gets, lists and cancels
// it with the same answers. Each test drives the cluster through its routes,
// its providers and its Redis alone, and waits only on what it can see: a
// request reaching a provider, a route's answer, or a key it removes.
import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// clusterWait bounds every wait for something a test expects to see.
const clusterWait = 10 * time.Second

// clusterBatchRedis connects replicas to the test Redis, each with a client
// of its own. Every key is under prefix, and is removed after the test.
func clusterBatchRedis(t *testing.T) (connect func() *redisstate.Client, prefix string) {
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
	dial := func() *redisstate.Client {
		client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
		if err != nil {
			t.Skipf("skipping: Redis not available at %s: %v", addr, err)
		}
		return client
	}
	prefix = fmt.Sprintf("test:cluster-batch:%s:%d:", t.Name(), time.Now().UnixNano())
	admin := dial()
	var clients []*redisstate.Client
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
	return func() *redisstate.Client {
		client := dial()
		clients = append(clients, client)
		return client
	}, prefix
}

// clusterUpstream is one replica's provider. It holds each request until
// the test lets it through (next, one at a time, or all), and reports every
// request that reached it on arrived: what its replica ran, and charged.
type clusterUpstream struct {
	url     string
	arrived chan struct{}
	next    chan struct{}
	all     chan struct{}
	once    sync.Once
	count   atomic.Int32
}

func newClusterUpstream(t *testing.T) *clusterUpstream {
	t.Helper()
	up := &clusterUpstream{arrived: make(chan struct{}, 64), next: make(chan struct{}), all: make(chan struct{})}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Only a chat request is an item; the gateway's own reachability
		// check of its provider is answered at once and counts for nothing.
		if r.Method != http.MethodPost {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		up.count.Add(1)
		up.arrived <- struct{}{}
		select {
		case <-up.next:
		case <-up.all:
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"chat-test","object":"chat.completion","model":"gpt-4o-mini","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`)
	}))
	t.Cleanup(func() {
		up.releaseAll()
		srv.Close()
	})
	up.url = srv.URL
	return up
}

// releaseAll answers every request, now and later.
func (up *clusterUpstream) releaseAll() { up.once.Do(func() { close(up.all) }) }

// awaitArrival waits for the next request to reach the provider.
func (up *clusterUpstream) awaitArrival(t *testing.T, what string) {
	t.Helper()
	select {
	case <-up.arrived:
	case <-time.After(clusterWait):
		t.Fatalf("%s: no request reached the provider within %s", what, clusterWait)
	}
}

// letOneThrough answers the request the provider holds.
func (up *clusterUpstream) letOneThrough(t *testing.T) {
	t.Helper()
	select {
	case up.next <- struct{}{}:
	case <-time.After(clusterWait):
		t.Fatalf("the provider held no request to answer within %s", clusterWait)
	}
}

// newClusterReplica is one cluster node with Redis: its own provider and
// Redis client, the nonce store every replica shares, and the test's key
// prefix.
func newClusterReplica(t *testing.T, client *redisstate.Client, prefix string, nonces *replicaNonceStore, up *clusterUpstream) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = delegated733Secret
	cfg.Auth.Enabled = true
	cfg.Cluster.Enabled = true
	cfg.Redis.Prefix = prefix
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.url + "/v1", APIFormat: "openai", Models: []string{"gpt-4o-mini"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	orgs := tenant.NewStore()
	for _, org := range []string{delegated733Org, list733OrgB} {
		orgs.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "test-only-org-provider", Models: []string{"gpt-4o-mini"}},
		}})
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), orgs, nil, client)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-10*time.Minute))
	srv.SetEnvelopeNonceStore(nonces)
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{
		{Org: delegated733Org, User: delegated733User, Workspace: "-"},
		{Org: list733OrgB, User: list733UserB, Workspace: "-"},
	}}, time.Now())
	return srv
}

// clusterBatchRig is two replicas of one gateway cluster, a and b, on the
// test Redis, each through its own client, and a client of the test's own
// for the keys it edits.
type clusterBatchRig struct {
	a, b                 *Server
	upA, upB             *clusterUpstream
	clientOfA, clientOfB *redisstate.Client
	admin                *redisstate.Client
	prefix               string
}

func newClusterBatchRig(t *testing.T) *clusterBatchRig {
	t.Helper()
	connect, prefix := clusterBatchRedis(t)
	rig := &clusterBatchRig{prefix: prefix, upA: newClusterUpstream(t), upB: newClusterUpstream(t)}
	rig.clientOfA, rig.clientOfB, rig.admin = connect(), connect(), connect()
	nonces := &replicaNonceStore{}
	rig.a = newClusterReplica(t, rig.clientOfA, prefix, nonces, rig.upA)
	rig.b = newClusterReplica(t, rig.clientOfB, prefix, nonces, rig.upB)
	return rig
}

// batchKey is the Redis key of id's part (":lease", ":cancel") under the
// gateway's batch prefix.
func (rig *clusterBatchRig) batchKey(id, part string) string {
	return rig.prefix + "batch:" + id + part
}

// expireLease ends the owner's lease on id, as its running out would.
func (rig *clusterBatchRig) expireLease(t *testing.T, id string) {
	t.Helper()
	if err := rig.admin.Redis().Del(context.Background(), rig.batchKey(id, ":lease")).Err(); err != nil {
		t.Fatalf("removing the lease of %s: %v", id, err)
	}
}

// clusterSubmit submits body on srv as org's person, and returns the batch
// id and the key id the batch records (dlg_ and the grant's id).
func clusterSubmit(t *testing.T, srv *Server, body, org, user string) (id, keyID string) {
	t.Helper()
	env, grant, fields := signedAs733(http.MethodPost, "/-/batches", body, "batch", org, user, delegated733Secret, time.Now().Unix())
	w := request733(srv, "/-/batches", body, env, grant)
	var out struct {
		ID string `json:"batch_id"`
	}
	if w.Code != http.StatusAccepted || json.Unmarshal(w.Body.Bytes(), &out) != nil || out.ID == "" {
		t.Fatalf("submit for %s: %d %s", org, w.Code, w.Body.String())
	}
	return out.ID, "dlg_" + strings.TrimPrefix(fields[14], "grant:")
}

// clusterList is org's person listing its batches on srv.
func clusterList(srv *Server, org, user string) *httptest.ResponseRecorder {
	env, grant, _ := signedAs733(http.MethodGet, "/-/batches", "", "batch", org, user, delegated733Secret, time.Now().Unix())
	return batchCall733(srv, http.MethodGet, "/-/batches", "", env, grant, "")
}

// adminList is the admin token's listing on srv.
func adminList(srv *Server) *httptest.ResponseRecorder {
	return batchCall733(srv, http.MethodGet, "/-/batches", "", "", "", delegated733Secret)
}

// clusterRead is the delegated get of id on srv for the first org's person:
// its status and error code, empty when it did not answer 200.
func clusterRead(srv *Server, id string) (status, code string) {
	w := asOrg733(srv, http.MethodGet, getPath733(id), "", "", delegated733Org, delegated733User)
	var out struct {
		Status    string `json:"status"`
		ErrorCode string `json:"error_code"`
	}
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil {
		return "", ""
	}
	return out.Status, out.ErrorCode
}

func clusterAnswer(t *testing.T, name string, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &out) != nil {
		t.Fatalf("%s: %d %s", name, w.Code, w.Body.String())
	}
	return out
}

// listedAs reports whether rows hold exactly one row for id, with status
// and code.
func listedAs(rows []map[string]any, id, status, code string) bool {
	found := 0
	for _, row := range rows {
		if row["batch_id"] != id {
			continue
		}
		found++
		got, _ := row["error_code"].(string)
		if row["status"] != status || got != code {
			return false
		}
	}
	return found == 1
}

// A batch submitted on one replica is got and listed on the other with the
// same answers as on its owner, its progress included, and only its owner
// runs its items.
func TestClusterBatchGetAndListAnswerTheSameOnEveryReplica(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")
	rig.upA.letOneThrough(t)
	rig.upA.awaitArrival(t, "the second item, once the first one's progress is stored")

	onA := clusterAnswer(t, "get on the owner", asOrg733(rig.a, http.MethodGet, getPath733(id), "", "", delegated733Org, delegated733User))
	onB := clusterAnswer(t, "get on the other replica", asOrg733(rig.b, http.MethodGet, getPath733(id), "", "", delegated733Org, delegated733User))
	if !reflect.DeepEqual(onA, onB) {
		t.Fatalf("the other replica answers\n%v\nthe owner answers\n%v", onB, onA)
	}
	if onB["status"] != "processing" || onB["batch_id"] != id || onB["total"] != float64(2) {
		t.Fatalf("the other replica's get: %v", onB)
	}

	listedA := list733Rows(t, clusterList(rig.a, delegated733Org, delegated733User))
	listedB := list733Rows(t, clusterList(rig.b, delegated733Org, delegated733User))
	if !reflect.DeepEqual(listedA, listedB) || len(listedB) != 1 || listedB[0]["batch_id"] != id {
		t.Fatalf("the other replica lists %v, the owner %v; want the same one batch %s", listedB, listedA, id)
	}
	if counts, _ := listedB[0]["counts"].(map[string]any); counts["completed"] != float64(1) {
		t.Fatalf("the other replica lists the batch with counts %v, want 1 completed", listedB[0]["counts"])
	}
	if ran := rig.upB.count.Load(); ran != 0 {
		t.Fatalf("the other replica ran %d items, want 0", ran)
	}
}

// FA's background reconciliation finds a batch whose submit answer it never
// received through the admin listing of any replica, by its org and key id;
// with the shared state unreadable, that listing answers 503, never a list
// without the batch.
func TestClusterBatchAdminListingOnAnyReplicaFindsASubmissionWhoseAnswerWasLost(t *testing.T) {
	rig := newClusterBatchRig(t)
	_, keyID := clusterSubmit(t, rig.a, list733BatchBody, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the batch's item")

	for _, replica := range []struct {
		name string
		srv  *Server
	}{{"the owner", rig.a}, {"the other replica", rig.b}} {
		matches := 0
		for _, row := range list733Rows(t, adminList(replica.srv)) {
			if row["key_id"] == keyID && row["org_id"] == delegated733Org {
				matches++
			}
		}
		if matches != 1 {
			t.Errorf("%s's admin listing holds %d batches with key %s, want 1", replica.name, matches, keyID)
		}
	}

	rig.clientOfB.Close()
	if w := adminList(rig.b); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("the admin listing without the batch store: %d %s, want 503", w.Code, w.Body.String())
	}
}

// A cancel sent to the other replica answers cancelled once the owner has
// stopped the batch: both replicas then read it cancelled, and no item
// starts after it.
func TestClusterBatchCancelOnTheOtherReplicaAnswersCancelledOnceTheOwnerStopped(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")

	out := clusterAnswer(t, "cancel on the other replica", asOrg733(rig.b, http.MethodPost, cancelPath733(id), "", "", delegated733Org, delegated733User))
	if len(out) != 3 || out["batch_id"] != id || out["org_id"] != delegated733Org || out["status"] != "cancelled" {
		t.Fatalf("the other replica's cancel answer: %v", out)
	}
	owned, ok := rig.a.batchProcessor.Get(id)
	if !ok || owned.GetStatus() != "cancelled" {
		t.Fatal("the other replica answered cancelled before the owner stopped the batch")
	}
	for _, replica := range []struct {
		name string
		srv  *Server
	}{{"the owner", rig.a}, {"the other replica", rig.b}} {
		if status, _ := clusterRead(replica.srv, id); status != "cancelled" {
			t.Errorf("%s reads the batch %q, want cancelled", replica.name, status)
		}
	}
	rig.upA.releaseAll()
	if ranA, ranB := rig.upA.count.Load(), rig.upB.count.Load(); ranA != 1 || ranB != 0 {
		t.Fatalf("the owner ran %d items and the other replica %d, want 1 and 0", ranA, ranB)
	}
}

// A cancel sent to the other replica while the owner cannot reach Redis
// stays a request: the batch reads processing on every replica that can
// read it, never cancelled, until the owner's lease runs out and it reads
// failed with owner_lost.
func TestClusterBatchCancelWhileTheOwnerIsCutOffFromRedisIsNeverReadAsCancelled(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")
	rig.clientOfA.Close()

	out := clusterAnswer(t, "cancel on the other replica", asOrg733(rig.b, http.MethodPost, cancelPath733(id), "", "", delegated733Org, delegated733User))
	if out["batch_id"] != id || out["status"] != "processing" {
		t.Fatalf("the other replica's cancel answer while the owner is cut off: %v, want processing", out)
	}
	if status, _ := clusterRead(rig.b, id); status != "processing" {
		t.Fatalf("the other replica reads the batch %q, want processing", status)
	}
	if rows := list733Rows(t, clusterList(rig.b, delegated733Org, delegated733User)); !listedAs(rows, id, "processing", "") {
		t.Fatalf("the other replica lists %v, want the batch processing", rows)
	}

	rig.expireLease(t, id)
	if status, code := clusterRead(rig.b, id); status != "failed" || code != "owner_lost" {
		t.Fatalf("the other replica reads the batch %q (%q) once the lease ran out, want failed (owner_lost)", status, code)
	}
	if rows := list733Rows(t, clusterList(rig.b, delegated733Org, delegated733User)); !listedAs(rows, id, "failed", "owner_lost") {
		t.Fatalf("the other replica lists %v, want the batch failed (owner_lost)", rows)
	}
}

// When the replica running a batch is gone, the other replica answers the
// batch as failed with owner_lost once the lease has run out, with its
// results and summary, never as not found, and runs none of its items.
func TestClusterBatchOfAReplicaThatIsGoneReadsAsOwnerLost(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")
	rig.clientOfA.Close()
	rig.expireLease(t, id)

	out := clusterAnswer(t, "get on the other replica", asOrg733(rig.b, http.MethodGet, getPath733(id), "", "", delegated733Org, delegated733User))
	if out["status"] != "failed" || out["error_code"] != "owner_lost" {
		t.Fatalf("the other replica's get of the lost batch: %v", out)
	}
	if results, _ := out["results"].([]any); len(results) != 2 {
		t.Fatalf("the lost batch's results: %v", out["results"])
	}
	if _, ok := out["summary"].(map[string]any); !ok {
		t.Fatalf("the lost batch has no summary: %v", out)
	}
	if rows := list733Rows(t, clusterList(rig.b, delegated733Org, delegated733User)); !listedAs(rows, id, "failed", "owner_lost") {
		t.Fatalf("the other replica lists %v, want the batch failed (owner_lost)", rows)
	}
	if ran := rig.upB.count.Load(); ran != 0 {
		t.Fatalf("the other replica ran %d items, want 0", ran)
	}
}

// Another org's batch is not found on either replica, byte for byte as an id
// no batch has, for get and cancel; it keeps running with no cancel request
// left, and that org lists nothing.
func TestClusterBatchOfAnotherOrgIsNotFoundOnEveryReplica(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")

	for _, replica := range []struct {
		name string
		srv  *Server
	}{{"the owner", rig.a}, {"the other replica", rig.b}} {
		for _, route := range batchRoutes733 {
			missing := asOrg733(replica.srv, route.method, route.path(missing733Batch), "", "", list733OrgB, list733UserB)
			if !notFound733(t, replica.name+" "+route.method+" a missing id", missing) {
				continue
			}
			got := asOrg733(replica.srv, route.method, route.path(id), "", "", list733OrgB, list733UserB)
			sameAnswer733(t, replica.name+" "+route.method+" another org's batch", missing, got)
		}
		if rows := list733Rows(t, clusterList(replica.srv, list733OrgB, list733UserB)); len(rows) != 0 {
			t.Errorf("%s lists %v for another org, want nothing", replica.name, rows)
		}
	}
	if n, err := rig.admin.Redis().Exists(context.Background(), rig.batchKey(id, ":cancel")).Result(); err != nil || n != 0 {
		t.Fatalf("another org's cancel left a cancel request: %d, %v", n, err)
	}
	if owned, ok := rig.a.batchProcessor.Get(id); !ok || owned.GetStatus() != "processing" {
		t.Fatal("the owner's batch is gone or no longer processing after another org's cancels")
	}
	if status, _ := clusterRead(rig.b, id); status != "processing" {
		t.Fatalf("the other replica reads the batch %q, want processing", status)
	}
	if ran := rig.upB.count.Load(); ran != 0 {
		t.Fatalf("the other replica ran %d items, want 0", ran)
	}
}

// With the admin token, an id that is not one this gateway issues, such as
// the name of another key the batch store keeps, is not found on every
// replica, byte for byte as an id no batch has, for get and cancel; the batch
// store is not asked, and the real batch still reads.
func TestClusterBatchIdOfAnotherShapeIsNotFoundOnEveryReplica(t *testing.T) {
	rig := newClusterBatchRig(t)
	id, _ := clusterSubmit(t, rig.a, running733Body, delegated733Org, delegated733User)
	rig.upA.awaitArrival(t, "the first item")
	rig.upA.letOneThrough(t)
	rig.upA.awaitArrival(t, "the second item, once the first one's result is stored")

	query := "?org_id=" + delegated733Org
	for _, replica := range []struct {
		name string
		srv  *Server
	}{{"the owner", rig.a}, {"the other replica", rig.b}} {
		for _, route := range batchRoutes733 {
			missing := batchCall733(replica.srv, route.method, route.path(missing733Batch), query, "", "", delegated733Secret)
			if !notFound733(t, replica.name+" "+route.method+" a missing id", missing) {
				continue
			}
			for _, other := range []string{id + ":results", id + ":lease", "org:" + delegated733Org, "all"} {
				got := batchCall733(replica.srv, route.method, route.path(other), query, "", "", delegated733Secret)
				sameAnswer733(t, replica.name+" "+route.method+" the id "+other, missing, got)
			}
		}
	}
	for _, key := range []string{":results:cancel", ":lease:cancel"} {
		if n, err := rig.admin.Redis().Exists(context.Background(), rig.batchKey(id, key)).Result(); err != nil || n != 0 {
			t.Fatalf("a cancel of another key's name left %s: %d, %v", key, n, err)
		}
	}
	if status, _ := clusterRead(rig.b, id); status != "processing" {
		t.Fatalf("the other replica reads the batch %q, want processing", status)
	}
}

// A cluster replica whose batch store cannot answer refuses a submission
// with 503 and runs nothing, and answers a get, a cancel and both listings
// with 503, never with not found or an empty list.
func TestClusterBatchRoutesAnswerUnavailableWhenTheStoreIs(t *testing.T) {
	rig := newClusterBatchRig(t)
	rig.clientOfB.Close()

	env, grant, _ := signedAs733(http.MethodPost, "/-/batches", list733BatchBody, "batch", delegated733Org, delegated733User, delegated733Secret, time.Now().Unix())
	if w := request733(rig.b, "/-/batches", list733BatchBody, env, grant); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("submit without the batch store: %d %s, want 503", w.Code, w.Body.String())
	}
	if rows := rig.b.batchProcessor.Inventory(); len(rows) != 0 {
		t.Fatalf("the refused submission left %d batches", len(rows))
	}
	for _, route := range batchRoutes733 {
		if w := asOrg733(rig.b, route.method, route.path(missing733Batch), "", "", delegated733Org, delegated733User); w.Code != http.StatusServiceUnavailable {
			t.Errorf("%s without the batch store: %d %s, want 503", route.method, w.Code, w.Body.String())
		}
	}
	if w := clusterList(rig.b, delegated733Org, delegated733User); w.Code != http.StatusServiceUnavailable {
		t.Errorf("the delegated listing without the batch store: %d %s, want 503", w.Code, w.Body.String())
	}
	if w := adminList(rig.b); w.Code != http.StatusServiceUnavailable {
		t.Errorf("the admin listing without the batch store: %d %s, want 503", w.Code, w.Body.String())
	}
	if ran := rig.upB.count.Load(); ran != 0 {
		t.Fatalf("the refused submission ran %d items", ran)
	}
}
