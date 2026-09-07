package propertycatalog

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }

// Only transport-focused tests use this explicit proof double. Production
// construction must supply real admission, a durable journal and a proof reader.
func testDurableSink(t *testing.T, cfg ClickHouseSinkConfig) (*ClickHouseSink, error) {
	t.Helper()
	doc := testWriteAdmission(t, 1, false)
	doc.Database, doc.Environment, doc.Members[0].URL = cfg.Database, cfg.Environment, cfg.URL
	doc.TopologySHA256 = admissionDigest(doc)
	policy, err := NewWritePolicy(doc)
	if err != nil {
		return nil, err
	}
	journal, err := OpenWriteAttemptJournal(t.TempDir())
	if err != nil {
		return nil, err
	}
	t.Cleanup(func() { journal.Close() })
	cfg.WritePolicy, cfg.WriteJournal, cfg.WriteProof = policy, journal, &explicitWriteProof{}
	return NewClickHouseSink(cfg)
}

func TestClickHouseSinkPinsOnlyNewCatalogTablesAndExactColumns(t *testing.T) {
	var queries []string
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if username, password, ok := request.BasicAuth(); !ok || username != "property_writer" || password != "secret" {
			t.Fatalf("basic auth=%q/%q ok=%v", username, password, ok)
		}
		body, err := io.ReadAll(request.Body)
		if err != nil || len(body) == 0 {
			t.Fatalf("body=%q err=%v", body, err)
		}
		queries = append(queries, request.URL.Query().Get("query"))
		if got := request.URL.Query().Get("database"); got != "property_catalog_dev_sink_test" {
			t.Fatalf("request database=%q", got)
		}
		if request.URL.Query().Get("async_insert") != "0" || request.URL.Query().Get("wait_end_of_query") != "1" {
			t.Fatalf("INSERT must pin synchronous execution and complete response buffering: %s", request.URL.RawQuery)
		}
		if !strings.Contains(request.URL.Query().Get("query"), "SETTINGS async_insert=0, insert_quorum=0, insert_quorum_parallel=1 FORMAT JSONEachRow") {
			t.Fatal("INSERT SQL must retain positive execution-settings evidence")
		}
		return &http.Response{
			StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader("")), Header: make(http.Header),
		}, nil
	})
	sink, err := testDurableSink(t, ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "property_catalog_dev_sink_test",
		Environment: DevelopmentEnvironment,
		Username:    "property_writer", Password: "secret", RequestTimeout: time.Second,
		RoundTripper: transport,
	})
	if err != nil {
		t.Fatal(err)
	}
	handler, _ := NewDeliveryHandler(sink, &recordingLeaseGuard{}, time.Second)
	handler.now = func() time.Time { return time.Date(2026, 8, 14, 1, 2, 3, 0, time.UTC) }
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 1)),
		Transport: TransportKafka, KafkaPartition: 1, KafkaOffset: 2,
	}); err != nil {
		t.Fatal(err)
	}
	valueHandler, _ := NewDeliveryHandler(sink, &recordingLeaseGuard{role: "values"}, time.Second)
	valueHandler.now = handler.now
	if err := valueHandler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, valueDeliveryInput(t, 1)),
		Transport: TransportKafka, KafkaPartition: 1, KafkaOffset: 3,
	}); err != nil {
		t.Fatal(err)
	}
	if len(queries) != 4 || !strings.Contains(queries[0], "INSERT INTO property_definition_catalog (") ||
		!strings.Contains(queries[0], strings.Join(definitionColumns, ",")) ||
		!strings.Contains(queries[1], "INSERT INTO property_catalog_deliveries (") ||
		!strings.Contains(queries[1], strings.Join(deliveryColumns, ",")) ||
		!strings.Contains(queries[2], "INSERT INTO span_attribute_value_catalog (") ||
		!strings.Contains(queries[2], strings.Join(attributeValueColumns, ",")) ||
		!strings.Contains(queries[3], "INSERT INTO property_catalog_deliveries (") ||
		!strings.Contains(queries[3], strings.Join(deliveryColumns, ",")) {
		t.Fatalf("queries=%v", queries)
	}
	if err := sink.InsertPropertyCatalog(context.Background(), Table("spans"), []map[string]any{{}}); err == nil ||
		!strings.Contains(err.Error(), "forbidden") {
		t.Fatalf("forbidden table error=%v", err)
	}
}

func TestClickHouseSinkRejectsUnsafeDestinationAndRowShapeBeforeIO(t *testing.T) {
	for _, cfg := range []ClickHouseSinkConfig{
		{URL: "http://clickhouse:8123/?query=DROP", Database: "property_catalog_dev_sink_test", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "catalog;DROP", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog_dev_sink_test", Environment: DevelopmentEnvironment, Username: ""},
		{URL: "http://clickhouse:8123", Database: "futureagi", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "default", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "system", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog_dev_real", Environment: ProductionEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog", Environment: "staging", Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog_backup", Environment: ProductionEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "property_catalog_dev_Bad", Environment: DevelopmentEnvironment, Username: "writer"},
		{URL: "http://clickhouse:8123", Database: "PROPERTY_catalog_dev_bad", Environment: DevelopmentEnvironment, Username: "writer"},
	} {
		if err := ValidateClickHouseDestination(cfg); err == nil {
			t.Fatalf("unsafe sink config accepted: %+v", cfg)
		}
	}
	called := false
	sink, err := testDurableSink(t, ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "property_catalog_dev_sink_test", Username: "writer",
		Environment: DevelopmentEnvironment,
		RoundTripper: roundTripFunc(func(*http.Request) (*http.Response, error) {
			called = true
			return nil, nil
		}),
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := sink.InsertPropertyCatalogDelivery(context.Background(), []map[string]any{{"spans": "forbidden"}}); err == nil {
		t.Fatal("delivery row with forbidden shape was accepted")
	}
	if called {
		t.Fatal("unsafe row reached HTTP transport")
	}
}

func TestClickHouseSinkAcceptsSafeLegacyDevelopmentCatalogName(t *testing.T) {
	if err := ValidateClickHouseDestination(ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "legacy_catalog_snapshot",
		Environment: DevelopmentEnvironment, Username: "property_writer",
	}); err != nil {
		t.Fatal(err)
	}
}

func TestClickHouseSinkAcceptsOnlyStableProductionCatalogName(t *testing.T) {
	if err := ValidateClickHouseDestination(ClickHouseSinkConfig{
		URL: "https://clickhouse:8443", Database: "property_catalog",
		Environment: ProductionEnvironment, Username: "property_writer",
	}); err != nil {
		t.Fatal(err)
	}
}

func TestClickHouseSinkBindsProductionToConfiguredCatalogDatabase(t *testing.T) {
	const isolated = "th7247_catalog_prod_20260823a"
	if err := ValidateClickHouseDestination(ClickHouseSinkConfig{
		URL: "https://clickhouse:8443", Database: isolated, ProductionDatabase: isolated,
		Environment: ProductionEnvironment, Username: "property_writer",
	}); err != nil {
		t.Fatal(err)
	}
	for _, cfg := range []ClickHouseSinkConfig{
		// The default name is no longer the production catalog once one is configured.
		{URL: "https://clickhouse:8443", Database: "property_catalog", ProductionDatabase: isolated, Environment: ProductionEnvironment, Username: "writer"},
		{URL: "https://clickhouse:8443", Database: isolated, Environment: ProductionEnvironment, Username: "writer"},
		// The configured production identity must itself be a safe isolated name.
		{URL: "https://clickhouse:8443", Database: "futureagi", ProductionDatabase: "futureagi", Environment: ProductionEnvironment, Username: "writer"},
		{URL: "https://clickhouse:8443", Database: "Bad-Name", ProductionDatabase: "Bad-Name", Environment: ProductionEnvironment, Username: "writer"},
		{URL: "https://clickhouse:8443", Database: "catalog;DROP", ProductionDatabase: "catalog;DROP", Environment: ProductionEnvironment, Username: "writer"},
		// Development may never write into the configured production catalog.
		{URL: "http://clickhouse:8123", Database: isolated, ProductionDatabase: isolated, Environment: DevelopmentEnvironment, Username: "writer"},
	} {
		if err := ValidateClickHouseDestination(cfg); err == nil {
			t.Fatalf("unsafe sink config accepted: %+v", cfg)
		}
	}
}

type insertAckBody struct {
	reader io.Reader
	read   int
	closed bool
}

func (b *insertAckBody) Read(p []byte) (int, error) {
	n, err := b.reader.Read(p)
	b.read += n
	return n, err
}

func (b *insertAckBody) Close() error {
	b.closed = true
	return nil
}

type insertAckErrorReader struct{ err error }

func (r insertAckErrorReader) Read([]byte) (int, error) { return 0, r.err }

func TestClickHouseSinkRequiresCompleteEmptyInsertAcknowledgement(t *testing.T) {
	const responseLimit = 4 << 10
	for _, test := range []struct {
		name          string
		body          string
		readError     error
		status        int
		contentLength int64
		exceptionCode string
		wantError     bool
	}{
		{name: "empty-success", status: http.StatusOK},
		{name: "chunked-empty-success", status: http.StatusOK, contentLength: -1},
		{name: "unexpected-EOF", status: http.StatusOK, readError: io.ErrUnexpectedEOF, wantError: true},
		{name: "partial-body-then-EOF", status: http.StatusOK, body: "Code: ", readError: io.ErrUnexpectedEOF, wantError: true},
		{name: "body-deadline", status: http.StatusOK, readError: context.DeadlineExceeded, wantError: true},
		{name: "body-canceled", status: http.StatusOK, readError: context.Canceled, wantError: true},
		{name: "declared-body-missing", status: http.StatusOK, contentLength: 10, wantError: true},
		{name: "CH-exception-after-200", status: http.StatusOK, body: "Code: 242. DB::Exception: Table is in readonly mode.\n", wantError: true},
		{name: "CH-exception-header", status: http.StatusOK, exceptionCode: "242", wantError: true},
		{name: "unexpected-JSON", status: http.StatusOK, body: `{"ok":true}`, wantError: true},
		{name: "whitespace-is-not-empty", status: http.StatusOK, body: "\n", wantError: true},
		{name: "body-at-limit", status: http.StatusOK, body: strings.Repeat(" ", responseLimit), wantError: true},
		{name: "body-over-limit", status: http.StatusOK, body: strings.Repeat(" ", responseLimit+1), wantError: true},
		{name: "exception-beyond-limit", status: http.StatusOK, body: strings.Repeat(" ", responseLimit+100) + "DB::Exception", wantError: true},
		{name: "non-200-empty", status: http.StatusServiceUnavailable, wantError: true},
		{name: "non-200-diagnostic", status: http.StatusInternalServerError, body: "DB::Exception", wantError: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			var reader io.Reader = strings.NewReader(test.body)
			if test.readError != nil {
				reader = io.MultiReader(reader, insertAckErrorReader{test.readError})
			}
			body := &insertAckBody{reader: reader}
			requests := 0
			sink, err := newClickHouseTransport(ClickHouseSinkConfig{
				URL: "http://unused.invalid", Database: "property_catalog_dev_sink_test",
				Environment: DevelopmentEnvironment, Username: "property_writer",
				RoundTripper: roundTripFunc(func(*http.Request) (*http.Response, error) {
					requests++
					header := make(http.Header)
					if test.exceptionCode != "" {
						header.Set("X-ClickHouse-Exception-Code", test.exceptionCode)
					}
					return &http.Response{
						StatusCode: test.status, Body: body, Header: header, ContentLength: test.contentLength,
					}, nil
				}),
			})
			if err != nil {
				t.Fatal(err)
			}
			// Exercise the one-shot transport ACK boundary directly. Journal and
			// consumer behavior is exercised below with mandatory explicit proof.
			err = sink.sendExact(boundedWriteTestContext(t), ExactWriteAttempt{
				Endpoint: "http://unused.invalid", Table: "property_catalog_deliveries",
				Body: []byte("{}\n"), Settings: map[string]string{"async_insert": "0", "wait_end_of_query": "1", "insert_quorum": "0", "insert_quorum_parallel": "1"},
			})
			if (err != nil) != test.wantError {
				t.Fatalf("insert error=%v want error=%v", err, test.wantError)
			}
			if test.readError != nil && !errors.Is(err, test.readError) {
				t.Fatalf("insert error=%v does not preserve read error %v", err, test.readError)
			}
			if requests != 1 || !body.closed || body.read > responseLimit+1 {
				t.Fatalf("requests=%d closed=%v response bytes=%d", requests, body.closed, body.read)
			}
		})
	}
}

func TestConsumerDoesNotCommitOrAdvanceOnAmbiguousInsertAcknowledgement(t *testing.T) {
	for _, stage := range []string{"data", "ledger"} {
		for _, failure := range []string{"truncated", "exception", "oversized"} {
			t.Run(stage+"/"+failure, func(t *testing.T) {
				var queries []string
				var bodies []*insertAckBody
				sink, err := testDurableSink(t, ClickHouseSinkConfig{
					URL: "http://unused.invalid", Database: "property_catalog_dev_sink_test",
					Environment: DevelopmentEnvironment, Username: "property_writer",
					RoundTripper: roundTripFunc(func(request *http.Request) (*http.Response, error) {
						query := request.URL.Query().Get("query")
						queries = append(queries, query)
						isLedger := strings.HasPrefix(query, "INSERT INTO property_catalog_deliveries (")
						var reader io.Reader = strings.NewReader("")
						if (stage == "ledger") == isLedger {
							switch failure {
							case "truncated":
								reader = insertAckErrorReader{io.ErrUnexpectedEOF}
							case "exception":
								reader = strings.NewReader("Code: 242. DB::Exception: incomplete insert\n")
							case "oversized":
								reader = strings.NewReader(strings.Repeat(" ", (4<<10)+100))
							}
						}
						body := &insertAckBody{reader: reader}
						bodies = append(bodies, body)
						return &http.Response{StatusCode: http.StatusOK, Body: body, Header: make(http.Header)}, nil
					}),
				})
				if err != nil {
					t.Fatal(err)
				}
				handler, err := NewDeliveryHandler(sink, &recordingLeaseGuard{}, time.Second)
				if err != nil {
					t.Fatal(err)
				}
				envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
				source := &oneRecordSource{record: kafkaRecord(t, envelope, 5)}
				validator, err := NewSequenceValidator(nil)
				if err != nil {
					t.Fatal(err)
				}
				consumer, err := NewConsumer("property-catalog", source, handler, validator)
				if err != nil {
					t.Fatal(err)
				}
				// Run must stop at the first ambiguity. Do not retry the unknown write.
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				err = consumer.Run(ctx)
				if err == nil || (failure == "truncated" && !errors.Is(err, io.ErrUnexpectedEOF)) {
					t.Fatalf("consumer error=%v", err)
				}
				wantRequests := 1
				if stage == "ledger" {
					wantRequests = 2
				}
				if source.commits != 0 || source.rebalances != 1 || len(queries) != wantRequests {
					t.Fatalf("commits=%d rebalances=%d requests=%v", source.commits, source.rebalances, queries)
				}
				validation, err := validator.Check(envelope) // Read-only sequence check, not a delivery retry.
				if err != nil || validation.Status != SequenceNext {
					t.Fatalf("ambiguous write advanced sequence: status=%s error=%v", validation.Status, err)
				}
				for _, body := range bodies {
					if !body.closed {
						t.Fatal("insert response body was not closed")
					}
				}
			})
		}
	}
}
