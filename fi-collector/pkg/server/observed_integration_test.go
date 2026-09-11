package server

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/pkg/kmsg"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// This test exercises real HTTP parsing/stamping, the existing async source
// flusher, ClickHouse, fsync spool recovery, Kafka, and the catalog consumer.
// Authentication is a synthetic trusted result, NOT a real PostgreSQL key
// validation. Only explicitly configured loopback test services are accepted.
func TestObservedCatalogHTTPToLocalStack(t *testing.T) {
	origin, broker := os.Getenv("OBS_TEST_CH_URL"), os.Getenv("OBS_TEST_KAFKA_BROKER")
	if origin == "" || broker == "" {
		t.Skip("set OBS_TEST_CH_URL and OBS_TEST_KAFKA_BROKER for isolated local integration")
	}
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Port() == "" || u.User != nil || u.RawQuery != "" || (u.Path != "" && u.Path != "/") {
		t.Fatal("ClickHouse integration requires an explicit loopback HTTP origin")
	}
	host, port, err := net.SplitHostPort(broker)
	if err != nil || host != "127.0.0.1" || port == "" {
		t.Fatal("Kafka integration requires an explicit loopback broker")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	suffix := fmt.Sprint(time.Now().UnixNano())
	sourceDB, catalogDB := "observed_http_source_"+suffix, "observed_http_catalog_"+suffix
	for _, database := range []string{sourceDB, catalogDB} {
		observedLocalSQL(t, origin, "default", "CREATE DATABASE "+database)
		t.Cleanup(func() { observedLocalSQL(t, origin, "default", "DROP DATABASE "+database) })
	}
	// Reuse the canonical columns/engine/projections unchanged. Storage-policy
	// and retention settings are deployment concerns absent from this fixture.
	sourceDDL := observedDDL(t, "schema", "002_spans_v2.sql")
	sourceDDL, _, found := strings.Cut(sourceDDL, "\nTTL ")
	if !found {
		t.Fatal("canonical source DDL boundary changed")
	}
	observedLocalSQL(t, origin, sourceDB, sourceDDL+"\nSETTINGS deduplicate_merge_projection_mode = 'rebuild'")
	for _, statement := range strings.Split(observedDDL(t, "observed_catalog", "schema.sql"), ";") {
		if strings.TrimSpace(statement) != "" {
			observedLocalSQL(t, origin, catalogDB, statement)
		}
	}
	cfg := observedcatalog.KafkaConfig{Brokers: []string{broker}, Topic: "observed-http-" + suffix, Group: "observed-http-group-" + suffix, Timeout: 10 * time.Second}
	admin, err := kgo.NewClient(kgo.SeedBrokers(broker))
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	create := kmsg.NewPtrCreateTopicsRequest()
	create.TimeoutMillis = 10000
	topic := kmsg.NewCreateTopicsRequestTopic()
	topic.Topic, topic.NumPartitions, topic.ReplicationFactor = cfg.Topic, 1, 1
	create.Topics = []kmsg.CreateTopicsRequestTopic{topic}
	created, err := create.RequestWith(ctx, admin)
	if err != nil || len(created.Topics) != 1 || created.Topics[0].ErrorCode != 0 {
		t.Fatalf("create isolated topic: %v %v", created, err)
	}
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		remove := kmsg.NewPtrDeleteTopicsRequest()
		remove.TopicNames, remove.TimeoutMillis = []string{cfg.Topic}, 10000
		if _, err := remove.RequestWith(cleanup, admin); err != nil {
			t.Errorf("delete isolated topic: %v", err)
		}
	}()
	spoolCfg := observedcatalog.SpoolConfig{Directory: t.TempDir()}
	spool, err := observedcatalog.NewWriter(spoolCfg, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if spool != nil {
			spool.Close()
		}
	}()
	writer, err := chwriter.New(chwriter.Config{URL: origin, Database: sourceDB, Table: "spans", Username: "test", Password: "test", MaxRetries: 1, RequestTimeout: 10 * time.Second, DeadLetterFile: filepath.Join(t.TempDir(), "source-deadletter.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	completion := &observedHandoff{Writer: spool, done: make(chan error, 1)}
	s := New(Config{BatchMaxRows: 1, BatchMaxAge: 10 * time.Millisecond}, writer, nil, NoopUsageEmitter{}, NoopMetering{}, WithPropertyCatalogWriter(completion))
	project := "33333333-3333-4333-8333-333333333333"
	result := &auth.ResolveResult{OrgID: "11111111-1111-4111-8111-111111111111", WorkspaceID: "22222222-2222-4222-8222-222222222222", Projects: map[string]string{"local-project": project}}
	httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Never derive this proof from incoming payload or headers.
		s.handleHTTPTraces(w, r.WithContext(auth.WithResolvedContext(r.Context(), result, "synthetic-test-proof")))
	}))
	defer httpServer.Close()
	traces := makeTraces("observed-http-smoke", "44444444-4444-4444-8444-444444444444")
	rs := traces.ResourceSpans().At(0)
	rs.Resource().Attributes().PutStr("project_name", "local-project")
	rs.Resource().Attributes().PutStr("fi.org_id", "55555555-5555-4555-8555-555555555555")
	span := rs.ScopeSpans().At(0).Spans().At(0)
	span.SetParentSpanID([8]byte{0xcc}) // no unrelated root-trace dimension write
	span.Attributes().PutStr("customer.label", "Hello")
	span.Attributes().PutDouble("customer.score", 1.5)
	span.Attributes().PutBool("customer.enabled", true)
	items := span.Attributes().PutEmptySlice("customer.items")
	items.AppendEmpty().SetStr("1")
	items.AppendEmpty().SetInt(1)
	items.AppendEmpty().SetBool(true)
	span.Attributes().PutEmptySlice("customer.empty")
	body, err := ptraceotlp.NewExportRequestFromTraces(traces).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	response, err := httpServer.Client().Post(httpServer.URL+"/v1/traces", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, response.Body)
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("OTLP HTTP status %d", response.StatusCode)
	}
	// Deliberately start the real flusher after the HTTP response to pin the
	// existing async acknowledgement contract without timing-dependent sleeps.
	if got := observedLocalSQL(t, origin, sourceDB, "SELECT count() FROM spans"); got != "0" {
		t.Fatal("request unexpectedly wrote synchronously", got)
	}
	s.wg.Add(1)
	go s.flushLoop()
	defer func() { close(s.stopCh); s.wg.Wait() }()
	select {
	case err := <-completion.done:
		if err != nil {
			t.Fatal("catalog handoff", err)
		}
	case <-ctx.Done():
		t.Fatal("canonical/spool handoff timed out", writer.Snapshot())
	}
	if stats := writer.Snapshot(); stats.RowsInserted != 1 || stats.RowsDeadLettered != 0 {
		t.Fatal("canonical insert failed", stats)
	}
	want := project + "\t" + result.OrgID + "\tHello\t1.5\t1"
	if got := observedLocalSQL(t, origin, sourceDB, "SELECT project_id, org_id, attrs_string['customer.label'], attrs_number['customer.score'], attrs_bool['customer.enabled'] FROM spans"); got != want {
		t.Fatalf("canonical scope/typed values: %q", got)
	}
	if got := observedLocalSQL(t, origin, sourceDB, "SELECT count() FROM system.columns WHERE database = '"+sourceDB+"' AND table = 'spans' AND name = 'workspace_id'"); got != "0" {
		t.Fatal("workspace sidecar leaked into canonical schema")
	}
	if err := spool.Close(); err != nil {
		t.Fatal(err)
	}
	spool, err = observedcatalog.NewWriter(spoolCfg, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	producer, err := observedcatalog.NewProducer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Close()
	if n, err := spool.Replay(ctx, producer); err != nil || n == 0 {
		t.Fatalf("reopen/publish durable handoff: %d %v", n, err)
	}
	if n, err := spool.Replay(ctx, producer); err != nil || n != 0 {
		t.Fatalf("acknowledged files retained: %d %v", n, err)
	}
	sink, err := observedcatalog.NewClickHouseSink(observedcatalog.ClickHouseConfig{URL: origin, Database: catalogDB, Username: "test", Password: "test"})
	if err != nil {
		t.Fatal(err)
	}
	consumer, err := observedcatalog.NewConsumer(cfg, sink)
	if err != nil {
		t.Fatal(err)
	}
	work, stop := context.WithCancel(ctx)
	consumerDone := make(chan error, 1)
	go func() { consumerDone <- consumer.Run(work) }()
	defer func() { stop(); <-consumerDone; consumer.Close() }()
	for {
		got := observedLocalSQL(t, origin, catalogDB, "SELECT count() FROM observed_attribute_values WHERE startsWith(attribute_key, 'customer.')")
		if got == "6" {
			break
		}
		select {
		case err := <-consumerDone:
			consumerDone <- err
			t.Fatal("consumer failed", err)
		case <-ctx.Done():
			t.Fatal("catalog did not receive all observations", got)
		case <-time.After(50 * time.Millisecond):
		}
	}
	if got := observedLocalSQL(t, origin, catalogDB, "SELECT count() FROM observed_attribute_keys WHERE startsWith(attribute_key, 'customer.')"); got != "5" {
		t.Fatal("empty array key or typed key missing", got)
	}
	if got := observedLocalSQL(t, origin, catalogDB, "SELECT count() FROM observed_attribute_values WHERE attribute_key = 'customer.items' AND attribute_type = 'array' AND value_json IN ('\"1\"', '1', 'true')"); got != "3" {
		t.Fatal("array member scalar kinds lost", got)
	}
	for _, table := range []string{observedcatalog.KeyTable, observedcatalog.ValueTable} {
		if got := observedLocalSQL(t, origin, catalogDB, "SELECT count() FROM "+table+" WHERE organization_id != '"+result.OrgID+"' OR workspace_id != '"+result.WorkspaceID+"' OR project_id != '"+project+"'"); got != "0" {
			t.Fatal("catalog used untrusted payload scope", table, got)
		}
	}
}

type observedHandoff struct {
	*observedcatalog.Writer
	done chan error
}

func (w *observedHandoff) EnqueueCanonicalSpans(spans []observedcatalog.ScopedSpan) error {
	err := w.Writer.EnqueueCanonicalSpans(spans)
	w.done <- err
	return err
}

func observedDDL(t *testing.T, directory, name string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", directory, name))
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		line, _, _ = strings.Cut(line, "--")
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func observedLocalSQL(t *testing.T, origin, database, sql string) string {
	t.Helper()
	query := url.Values{"database": {database}, "wait_end_of_query": {"1"}}
	req, err := http.NewRequest(http.MethodPost, origin+"?"+query.Encode(), strings.NewReader(sql))
	if err != nil {
		t.Fatal(err)
	}
	req.SetBasicAuth("test", "test")
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 64<<10))
	if err != nil || response.StatusCode != http.StatusOK || response.Header.Get("X-ClickHouse-Exception-Code") != "" || strings.Contains(string(body), "DB::Exception") {
		t.Fatalf("local fixture SQL failed: %s (%v)", body, err)
	}
	return strings.TrimSpace(string(body))
}
