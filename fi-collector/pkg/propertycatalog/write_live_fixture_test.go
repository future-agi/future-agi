package propertycatalog

// Opt-in disposable-server probe. The Python ownership runner supplies its
// private manifest only after checking exact local Docker resources. Ordinary
// go test never contacts ClickHouse. No Kafka, lease, or publication is faked.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

var durableFixture = flag.String("catalog-durable-fixture", "", "exact private replicated_smoke run directory (opt-in live test)")

type fixtureExchange struct {
	Endpoint   string            `json:"endpoint"`
	User       string            `json:"user"`
	Query      string            `json:"query"`
	QueryID    string            `json:"query_id,omitempty"`
	Token      string            `json:"token,omitempty"`
	BodySHA256 string            `json:"body_sha256"`
	Settings   map[string]string `json:"settings"`
	Status     int               `json:"status"`
	Response   string            `json:"response"`
	Error      string            `json:"error,omitempty"`
	LostACK    bool              `json:"lost_ack"`
}

// Observe the real server response, then lose exactly one complete INSERT ACK
// at the caller's read boundary. Never redirect, replace proof rows, or resend.
type fixtureTransport struct {
	base       http.RoundTripper
	exchanges  []fixtureExchange
	loseLedger bool
	lost       int
}
type fixtureReadFailure struct{}

func (fixtureReadFailure) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }

func (f *fixtureTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if len(f.exchanges) >= 1500 {
		return nil, errors.New("fixture HTTP evidence bound exceeded")
	}
	raw, err := io.ReadAll(io.LimitReader(req.Body, maxCatalogInsertBytes+1))
	if err != nil || len(raw) > maxCatalogInsertBytes {
		return nil, errors.New("fixture request exceeds bound")
	}
	req.Body = io.NopCloser(bytes.NewReader(raw))
	q := req.URL.Query()
	user, _, _ := req.BasicAuth()
	query := q.Get("query")
	if query == "" {
		query = string(raw)
	}
	e := fixtureExchange{Endpoint: req.URL.Scheme + "://" + req.URL.Host, User: user, Query: query,
		QueryID: q.Get("query_id"), Token: q.Get("insert_deduplication_token"), BodySHA256: sha256Hex(raw), Settings: map[string]string{}}
	for key, values := range q {
		if key != "query" {
			e.Settings[key] = values[0]
		}
	}
	response, err := f.base.RoundTrip(req)
	if err != nil {
		e.Error = err.Error()
		f.exchanges = append(f.exchanges, e)
		return nil, err
	}
	body, readErr := io.ReadAll(io.LimitReader(response.Body, maxWriteProofBytes+1))
	response.Body.Close()
	e.Status = response.StatusCode
	if len(body) > maxWriteProofBytes {
		readErr = errors.New("fixture response exceeds evidence bound")
		body = body[:maxWriteProofBytes]
	}
	e.Response = string(body)
	response.Body = io.NopCloser(bytes.NewReader(body))
	if readErr != nil {
		e.Error = readErr.Error()
		response.Body = io.NopCloser(io.MultiReader(bytes.NewReader(body), fixtureReadFailure{}))
	}
	complete := readErr == nil && response.StatusCode == 200 && len(body) == 0 &&
		(response.Header.Get("X-ClickHouse-Exception-Code") == "" || response.Header.Get("X-ClickHouse-Exception-Code") == "0")
	if f.loseLedger && f.lost == 0 && strings.HasPrefix(query, "INSERT INTO property_catalog_deliveries ") && complete {
		f.lost++
		e.LostACK = true
		response.Body = io.NopCloser(fixtureReadFailure{})
	}
	f.exchanges = append(f.exchanges, e)
	return response, nil
}

func fixtureSave(path string, value any) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	fd, err := unix.Open(path, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	if _, err = f.Write(append(raw, '\n')); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

func fixtureEnvelope(t *testing.T, id InstallationIdentity, sequence uint64) WireEnvelope {
	t.Helper()
	d, v := testDefinition(), testValue()
	d.CatalogEpoch = id.CatalogEpoch
	d.ProjectionVersion = id.ProjectionVersion
	d.ProducerStreamID = id.ProducerStreamID
	d.ProducerSequence = sequence
	v.CatalogEpoch = id.CatalogEpoch
	refreshDefinitionHashes(t, &d)
	payload, err := BuildPayload([]DefinitionRow{d}, []AttributeValueRow{v}, 1, MaxChunkBytes, 1, testDigest(fmt.Sprintf("owned-fixture-%d", sequence)))
	if err != nil {
		t.Fatal(err)
	}
	return mustEnvelope(t, EnvelopeInput{OrganizationID: d.OrganizationID, WorkspaceID: d.WorkspaceID, CatalogEpoch: id.CatalogEpoch,
		CatalogRevision: d.CatalogRevision, ProjectionVersion: id.ProjectionVersion, BuildToken: d.BuildToken,
		SourceAdapter: d.SourceAdapter, SourceVersion: d.SourceVersion, SourceFingerprint: d.SourceFingerprint,
		ProducerStreamID: id.ProducerStreamID, Sequence: sequence, PreviousPayloadSHA256: ZeroSHA256, Payload: payload})
}

func fixtureLedger(e EnvelopeSnapshot) map[string]any {
	now := time.Now().UTC()
	return map[string]any{"organization_id": e.OrganizationID, "workspace_id": e.WorkspaceID,
		"catalog_epoch": e.CatalogEpoch, "catalog_revision": e.CatalogRevision, "build_token": e.BuildToken, "projection_version": e.ProjectionVersion,
		"source_adapter": string(e.SourceAdapter), "producer_stream_id": e.ProducerStreamID, "sequence": e.Sequence,
		"envelope_format": e.Format, "envelope_version": e.Version, "envelope_id": e.EnvelopeID, "payload_sha256": e.PayloadSHA256,
		"previous_payload_sha256": e.PreviousPayloadSHA256, "source_batch_digest": e.Payload.SourceBatchDigest,
		"outcome": string(e.Payload.Outcome), "terminal": uint8(0), "gap_reasons": append([]string{}, e.Payload.GapReasons...),
		"source_rows": e.Payload.SourceRows, "definition_rows": e.Payload.DefinitionRows, "value_rows": e.Payload.ValueRows, "tombstone_rows": e.Payload.TombstoneRows,
		"transport": TransportDirect, "kafka_partition": int32(-1), "kafka_offset": int64(-1), "delivered_at": now.Format(dateTime64Layout), "_version": uint64(now.UnixNano())}
}

func fixtureCall(call func(context.Context) error) error {
	ctx, cancel := context.WithTimeout(context.Background(), MaxDeliveryTimeout)
	defer cancel()
	return call(ctx)
}

func fixtureData(t *testing.T, sink *ClickHouseSink, e WireEnvelope) {
	t.Helper()
	if err := fixtureCall(func(ctx context.Context) error { return sink.BeginPropertyCatalogDelivery(ctx, e) }); err != nil {
		t.Fatal(err)
	}
	for _, chunk := range e.Snapshot().Payload.Chunks {
		rows, err := strictDecodeJSONEachRow(chunk.JSONEachRow, chunk.Table)
		if err != nil {
			t.Fatal(err)
		}
		if err = fixtureCall(func(ctx context.Context) error {
			return sink.InsertPropertyCatalog(withCatalogWriteIdentity(ctx, e.EnvelopeID(), fmt.Sprintf("chunk:%d", chunk.Index), false), chunk.Table, rows)
		}); err != nil {
			t.Fatalf("data %s: %v", chunk.Table, err)
		}
	}
}

func TestDurableClickHouseOwnedFixture(t *testing.T) {
	if *durableFixture == "" {
		t.Skip("requires an explicitly owned disposable ClickHouse fixture")
	}
	directory := *durableFixture
	if !filepath.IsAbs(directory) || filepath.Clean(directory) != directory || filepath.Base(filepath.Dir(directory)) != "runs" ||
		!strings.HasSuffix(filepath.Dir(directory), "/futureagi/scripts/property_catalog_oss/tests/replicated_smoke/runs") ||
		!regexp.MustCompile(`^pcreplicated-[a-f0-9]{16}$`).MatchString(filepath.Base(directory)) {
		t.Fatal("not an exact replicated_smoke run path")
	}
	parent, err := openPrivateDirectory(directory)
	if err != nil {
		t.Fatal(err)
	}
	defer parent.Close()
	raw, err := readJournalRegular(int(parent.Fd()), "manifest.json", 128<<10)
	if err != nil {
		t.Fatal(err)
	}
	var m struct {
		RunID    string         `json:"run_id"`
		Project  string         `json:"project"`
		Password string         `json:"password"`
		Ports    map[string]int `json:"ports"`
	}
	if json.Unmarshal(raw, &m) != nil || m.Project != filepath.Base(directory) || m.Project != "pcreplicated-"+m.RunID || !isLowerSHA256(m.Password) {
		t.Fatal("invalid fixture identity")
	}
	for _, node := range []string{"replica1", "replica2"} {
		if m.Ports[node+"_http"] <= 1024 || m.Ports[node+"_http"] > 65535 {
			t.Fatal("invalid fixture loopback port")
		}
	}
	if err = fixtureSave(filepath.Join(directory, "go-execute-intent.json"), map[string]any{"run_id": m.RunID, "no_replay": true}); err != nil {
		t.Fatal(err)
	}
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.Proxy = nil
	defer base.CloseIdleConnections()
	transport := &fixtureTransport{base: base}
	results := map[string]any{"run_id": m.RunID, "production_admitted": false, "kafka_tested": false, "lease_or_activation_tested": false}
	defer func() {
		results["failed"] = t.Failed()
		results["lost_full_ack_count"] = transport.lost
		results["exchanges"] = transport.exchanges
		if err := fixtureSave(filepath.Join(directory, "go-durable-result.json"), results); err != nil {
			t.Error(err)
		}
	}()
	for _, family := range []string{"standalone", "replicated"} {
		runtime := filepath.Join(directory, "admission-runtime-"+family)
		id, err := LoadInstallationIdentity(filepath.Join(runtime, InstallationIdentityFilename))
		if err != nil {
			t.Fatal(err)
		}
		database := "property_catalog_dev_" + family + "_" + m.RunID
		if id.Environment != "development" || id.TargetDatabase != database || id.OrderedTopic != m.Project+".ordered" {
			t.Fatal("foreign producer identity")
		}
		policy, err := LoadWritePolicy(runtime, "development", database, id.OrderedTopic)
		if err != nil {
			t.Fatal(err)
		}
		want := 1
		if family == "replicated" {
			want = 2
		}
		if policy.admission.Family != family || len(policy.admission.Members) != want {
			t.Fatal("wrong fixture topology")
		}
		for _, member := range policy.admission.Members {
			port, ok := m.Ports[member.Name+"_http"]
			if !ok || member.URL != fmt.Sprintf("http://127.0.0.1:%d", port) || member.Hostname != m.Project+"-"+member.Name {
				t.Fatal("unowned direct admission route")
			}
		}
		cfg := ClickHouseSinkConfig{URL: policy.admission.Members[0].URL, Database: database, Environment: "development", Username: "smoke_go_writer_" + family,
			Password: m.Password, RequestTimeout: MaxDeliveryTimeout, RoundTripper: transport, InstallationDirectory: runtime, OrderedTopic: id.OrderedTopic}
		read := cfg
		read.Username = "smoke_go_proof_" + family
		sink, _, journal, err := ConfigureDurableClickHouse(cfg, read)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { journal.Close() }()
		var logging []map[string]json.RawMessage
		err = fixtureCall(func(ctx context.Context) error {
			writerProbe := &HTTPWriteProof{reader: sink, policy: policy}
			logging, err = writerProbe.query(ctx, cfg.URL, `SELECT
 toString(getSetting('log_queries')) AS log_queries,
 toString(getSetting('log_query_settings')) AS log_query_settings,
 toString(getSetting('log_queries_probability')) AS log_queries_probability,
 toString(getSetting('log_queries_min_query_duration_ms')) AS log_queries_min_query_duration_ms,
 toString(getSetting('async_insert')) AS inherited_async_insert,
 toString(getSetting('wait_for_async_insert')) AS inherited_wait_for_async_insert
 FORMAT JSONEachRow`, nil, 4096)
			return err
		})
		if err != nil {
			t.Fatalf("actual writer logging/settings: %v", err)
		}
		results[family+"_actual_writer_profile"] = logging
		e := fixtureEnvelope(t, id, 1)
		fixtureData(t, sink, e)
		ledger := fixtureLedger(e.Snapshot())
		insertLedger := func(ctx context.Context) error {
			return sink.InsertPropertyCatalogDelivery(withCatalogWriteIdentity(ctx, e.EnvelopeID(), "delivery", false), []map[string]any{ledger})
		}
		if err := fixtureCall(insertLedger); err != nil {
			t.Fatalf("%s ledger: %v", family, err)
		}
		writesBefore := fixtureWriteCount(transport.exchanges)
		if err := fixtureCall(func(ctx context.Context) error { return sink.VerifyPropertyCatalogDelivery(ctx, e) }); err != nil {
			t.Fatalf("%s duplicate: %v", family, err)
		}
		if fixtureWriteCount(transport.exchanges) != writesBefore {
			t.Fatal("duplicate dispatched another INSERT")
		}
		results[family] = map[string]any{"status": "passed", "members": want, "topology_sha256": policy.admission.TopologySHA256, "three_table_dispatch": true, "duplicate_no_dispatch": true}
		if family == "replicated" {
			e = fixtureEnvelope(t, id, 2)
			fixtureData(t, sink, e)
			ledger = fixtureLedger(e.Snapshot())
			transport.loseLedger = true
			if err := fixtureCall(insertLedger); !errors.Is(err, ErrWriteUnresolved) {
				t.Fatalf("lost ACK must be unresolved: %v", err)
			}
			if transport.lost != 1 {
				t.Fatal("full ACK loss was not injected exactly once")
			}
			token := "property-catalog-v1:" + e.EnvelopeID() + ":delivery"
			lease, err := journal.Lock(token)
			if err != nil {
				t.Fatal(err)
			}
			sent, err := lease.Load()
			lease.Close()
			if err != nil || sent.State != AttemptSent || sent.SettlementSHA256 != "" {
				t.Fatalf("not durable Sent: %s %v", sent.State, err)
			}
			results["lost_ack_sent_attempt"] = sent
			if err := journal.Close(); err != nil {
				t.Fatal(err)
			}
			sink, _, journal, err = ConfigureDurableClickHouse(cfg, read)
			if err != nil {
				t.Fatal(err)
			}
			writesBefore = fixtureWriteCount(transport.exchanges)
			deadline := time.Now().Add(25 * time.Second)
			for {
				err = fixtureCall(insertLedger)
				if err == nil {
					break
				}
				if !errors.Is(err, ErrWriteUnresolved) || time.Now().After(deadline) {
					t.Fatalf("positive QueryFinish resolution unavailable: %v", err)
				}
				time.Sleep(time.Second) // bounded READ-only resolution; executor cannot resend Sent
			}
			if err := fixtureCall(func(ctx context.Context) error { return sink.VerifyPropertyCatalogDelivery(ctx, e) }); err != nil {
				t.Fatal(err)
			}
			if fixtureWriteCount(transport.exchanges) != writesBefore {
				t.Fatal("restart/duplicate replayed an INSERT")
			}
			lease, err = journal.Lock(token)
			if err != nil {
				t.Fatal(err)
			}
			settled, err := lease.Load()
			lease.Close()
			if err != nil || settled.State != AttemptComplete || !isLowerSHA256(settled.SettlementSHA256) || settled.QueryID != sent.QueryID || settled.BodySHA256 != sent.BodySHA256 {
				t.Fatal("exact lost-ACK receipt did not complete")
			}
			results["lost_ack_completed_attempt"] = settled
			var finish []map[string]json.RawMessage
			err = fixtureCall(func(ctx context.Context) error {
				finish, err = sink.writer.proof.(*HTTPWriteProof).query(ctx, settled.Endpoint,
					`SELECT query_id,type,exception_code,query,written_rows,Settings FROM system.query_log
 WHERE query_id={attempt_id:String} AND is_initial_query=1 AND type='QueryFinish'
 ORDER BY event_time_microseconds LIMIT 2 FORMAT JSONEachRow`, map[string]string{"param_attempt_id": settled.QueryID}, 64<<10)
				return err
			})
			if err != nil || len(finish) != 1 {
				t.Fatalf("actual QueryFinish evidence: %v", err)
			}
			results["actual_lost_ack_query_finish"] = finish
			t.Log("replicated lost full ACK: reopened durable Sent; actual QueryFinish + all-node coverage; zero INSERT retransmission")
		}
		t.Logf("%s: actual producer + three INSERT tables + all-member coverage + duplicate proof passed", family)
	}
}

func fixtureWriteCount(exchanges []fixtureExchange) int {
	n := 0
	for _, e := range exchanges {
		if strings.HasPrefix(e.Query, "INSERT ") {
			n++
		}
	}
	return n
}

func TestFixtureFullACKLossIsSingleAndReadBoundaryOnly(t *testing.T) {
	base := &proofRoundTripperFunc{fn: func(req *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(""))}, nil
	}}
	f := &fixtureTransport{base: base, loseLedger: true}
	for i := 0; i < 2; i++ {
		req, _ := http.NewRequest(http.MethodPost, "http://127.0.0.1:8123/?query=INSERT+INTO+property_catalog_deliveries+%28x%29+FORMAT+JSONEachRow", strings.NewReader("{}\n"))
		r, err := f.RoundTrip(req)
		if err != nil {
			t.Fatal(err)
		}
		_, err = io.ReadAll(r.Body)
		r.Body.Close()
		if (i == 0) != errors.Is(err, io.ErrUnexpectedEOF) {
			t.Fatalf("loss %d: %v", i, err)
		}
	}
	if f.lost != 1 || len(f.exchanges) != 2 || !f.exchanges[0].LostACK || f.exchanges[1].LostACK {
		t.Fatal("fault not exactly once")
	}
}

type proofRoundTripperFunc struct {
	fn func(*http.Request) (*http.Response, error)
}

func (f *proofRoundTripperFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f.fn(r) }
