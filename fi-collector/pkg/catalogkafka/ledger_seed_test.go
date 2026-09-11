package catalogkafka

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

const (
	ledgerTestProject  = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	ledgerTestStream   = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	ledgerDirectStream = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
)

func ledgerDigest(character string) string { return strings.Repeat(character, 64) }

func validLedgerRow(sequence, version uint64, payload, previous string) deliveryLedgerRow {
	return deliveryLedgerRow{
		ProjectID: ledgerTestProject, CatalogEpoch: 7, ProducerStreamID: ledgerTestStream,
		Sequence: sequence, EnvelopeFormat: EnvelopeFormat, EnvelopeVersion: EnvelopeVersion,
		EnvelopeID: ledgerDigest("a"), PayloadSHA256: payload,
		PreviousPayloadSHA256: previous, Transport: kafkaDeliveryTransport, Version: version,
	}
}

func encodeLedgerRows(t *testing.T, rows ...deliveryLedgerRow) []byte {
	t.Helper()
	var body strings.Builder
	encoder := json.NewEncoder(&body)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	return []byte(body.String())
}

func TestDeliveryLedgerReaderUsesOnlyFixedCatalogTableAndSeparateCredentials(t *testing.T) {
	first := validLedgerRow(1, 2, ledgerDigest("1"), ZeroSHA256)
	firstOld := first
	firstOld.Version = 1
	second := validLedgerRow(2, 3, ledgerDigest("2"), first.PayloadSHA256)
	second.EnvelopeID = ledgerDigest("b")
	direct := validLedgerRow(1, 1, ledgerDigest("3"), ZeroSHA256)
	direct.ProducerStreamID = ledgerDirectStream
	direct.Transport = directDeliveryTransport
	direct.EnvelopeFormat = "futureagi.span-attribute-catalog-job"
	direct.EnvelopeVersion = 2

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			t.Errorf("method=%s", request.Method)
		}
		username, password, ok := request.BasicAuth()
		if !ok || username != "ledger-reader" || password != "ledger-secret" {
			t.Errorf("ledger credentials=%q/%q present=%v", username, password, ok)
		}
		query := request.URL.Query()
		if query.Get("database") != "catalog_dev" || query.Get("readonly") != "1" ||
			query.Get("max_execution_time") != "1.500000" || query.Get("max_threads") != "1" ||
			query.Get("max_rows_to_read") != "100000" || query.Get("read_overflow_mode") != "throw" ||
			query.Get("max_result_bytes") != "8388608" ||
			query.Get("max_result_rows") != "100000" || query.Get("result_overflow_mode") != "throw" ||
			query.Get("output_format_json_quote_64bit_integers") != "0" {
			t.Errorf("query settings=%v", query)
		}
		body, err := io.ReadAll(request.Body)
		if err != nil {
			t.Error(err)
		}
		if string(body) != deliveryLedgerCheckpointQuery {
			t.Errorf("unexpected query:\n%s", body)
		}
		if strings.Count(string(body), catalogDeliveryTableForTest) != 1 {
			t.Errorf("query does not contain exactly one closed ledger target: %q", body)
		}
		writer.Header().Set("Content-Type", "application/x-ndjson")
		_, _ = writer.Write(encodeLedgerRows(t, firstOld, direct, second, first))
	}))
	defer server.Close()

	reader, err := NewDeliveryLedgerCheckpointReader(DeliveryLedgerReaderConfig{
		URL: server.URL, Database: "catalog_dev", Username: "ledger-reader", Password: "ledger-secret",
		RequestTimeout: 1500 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	checkpoints, err := reader.Load(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := []StreamCheckpoint{{
		ProjectID: ledgerTestProject, CatalogEpoch: 7, ProducerStreamID: ledgerTestStream,
		Sequence: 2, PayloadSHA256: second.PayloadSHA256, EnvelopeID: second.EnvelopeID,
	}}
	if !reflect.DeepEqual(checkpoints, want) {
		t.Fatalf("checkpoints=%+v want=%+v", checkpoints, want)
	}
}

// Kept separate from the implementation constant so this assertion catches
// an accidental widening or table rename in the fixed query.
const catalogDeliveryTableForTest = "span_attribute_catalog_deliveries"

func TestDeliveryLedgerCheckpointSeedsDuplicateAndNextSequence(t *testing.T) {
	first := mustEnvelope(t, testEnvelopeInput(t))
	secondInput := testEnvelopeInput(t)
	secondInput.Sequence = 2
	secondInput.PreviousPayloadSHA256 = first.PayloadSHA256()
	second := mustEnvelope(t, secondInput)

	firstSnapshot := first.Snapshot()
	secondSnapshot := second.Snapshot()
	rows := []deliveryLedgerRow{
		ledgerRowFromSnapshot(firstSnapshot, 1),
		ledgerRowFromSnapshot(secondSnapshot, 2),
	}
	checkpoints, err := checkpointsFromDeliveryLedger(rows)
	if err != nil {
		t.Fatal(err)
	}
	validator, err := NewSequenceValidator(checkpoints)
	if err != nil {
		t.Fatal(err)
	}
	duplicate, err := validator.Check(second)
	if err != nil || duplicate.Status != SequenceExactDuplicate {
		t.Fatalf("restart duplicate status=%q err=%v", duplicate.Status, err)
	}

	thirdInput := testEnvelopeInput(t)
	thirdInput.Sequence = 3
	thirdInput.PreviousPayloadSHA256 = second.PayloadSHA256()
	third := mustEnvelope(t, thirdInput)
	next, err := validator.Check(third)
	if err != nil || next.Status != SequenceNext {
		t.Fatalf("restart next status=%q err=%v", next.Status, err)
	}
}

func ledgerRowFromSnapshot(snapshot EnvelopeSnapshot, version uint64) deliveryLedgerRow {
	return deliveryLedgerRow{
		ProjectID: snapshot.ProjectID, CatalogEpoch: snapshot.CatalogEpoch,
		ProducerStreamID: snapshot.ProducerStreamID, Sequence: snapshot.Sequence,
		EnvelopeFormat: snapshot.Format, EnvelopeVersion: snapshot.Version,
		EnvelopeID: snapshot.EnvelopeID, PayloadSHA256: snapshot.PayloadSHA256,
		PreviousPayloadSHA256: snapshot.PreviousPayloadSHA256,
		Transport:             kafkaDeliveryTransport, Version: version,
	}
}

func TestDeliveryLedgerValidationFailsClosed(t *testing.T) {
	first := validLedgerRow(1, 10, ledgerDigest("1"), ZeroSHA256)
	second := validLedgerRow(2, 11, ledgerDigest("2"), first.PayloadSHA256)
	second.EnvelopeID = ledgerDigest("b")

	tests := []struct {
		name string
		rows []deliveryLedgerRow
		want string
	}{
		{
			name: "conflicting latest replacement",
			rows: func() []deliveryLedgerRow {
				conflict := first
				conflict.PayloadSHA256 = ledgerDigest("f")
				conflict.Version++
				return []deliveryLedgerRow{first, conflict}
			}(),
			want: "conflicting rows",
		},
		{
			name: "sequence gap",
			rows: []deliveryLedgerRow{first, func() deliveryLedgerRow {
				row := second
				row.Sequence = 3
				return row
			}()},
			want: "require contiguous 2",
		},
		{
			name: "broken chain",
			rows: []deliveryLedgerRow{first, func() deliveryLedgerRow {
				row := second
				row.PreviousPayloadSHA256 = ledgerDigest("e")
				return row
			}()},
			want: "breaks payload chain",
		},
		{
			name: "mixed transport",
			rows: []deliveryLedgerRow{first, func() deliveryLedgerRow {
				row := second
				row.Transport = directDeliveryTransport
				return row
			}()},
			want: "mixes Kafka and non-Kafka transports",
		},
		{
			name: "unknown format",
			rows: []deliveryLedgerRow{func() deliveryLedgerRow {
				row := first
				row.EnvelopeFormat = "old-format"
				return row
			}()},
			want: "unsupported envelope",
		},
		{
			name: "invalid digest",
			rows: []deliveryLedgerRow{func() deliveryLedgerRow {
				row := first
				row.PayloadSHA256 = "BAD"
				return row
			}()},
			want: "invalid envelope or payload digest",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := checkpointsFromDeliveryLedger(test.rows)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error=%v want substring %q", err, test.want)
			}
		})
	}
}

func TestDeliveryLedgerLatestReplacementAndExactDuplicateAreDeterministic(t *testing.T) {
	latest := validLedgerRow(1, 2, ledgerDigest("1"), ZeroSHA256)
	old := latest
	old.Version = 1
	rows := []deliveryLedgerRow{latest, old, latest}
	checkpoints, err := checkpointsFromDeliveryLedger(rows)
	if err != nil {
		t.Fatal(err)
	}
	if len(checkpoints) != 1 || checkpoints[0].PayloadSHA256 != latest.PayloadSHA256 {
		t.Fatalf("checkpoints=%+v", checkpoints)
	}
}

func TestDeliveryLedgerReaderBoundsAndErrors(t *testing.T) {
	valid := DeliveryLedgerReaderConfig{
		URL: "http://clickhouse:8123", Database: "catalog_dev", Username: "reader",
	}
	mutations := []struct {
		name string
		edit func(*DeliveryLedgerReaderConfig)
		want string
	}{
		{"non HTTP URL", func(cfg *DeliveryLedgerReaderConfig) { cfg.URL = "ftp://clickhouse" }, "absolute http"},
		{"embedded credentials", func(cfg *DeliveryLedgerReaderConfig) { cfg.URL = "http://u:p@clickhouse" }, "must not contain credentials"},
		{"bad database", func(cfg *DeliveryLedgerReaderConfig) { cfg.Database = "catalog; SELECT * FROM spans" }, "unquoted identifier"},
		{"missing username", func(cfg *DeliveryLedgerReaderConfig) { cfg.Username = "" }, "username is required"},
		{"timeout above cap", func(cfg *DeliveryLedgerReaderConfig) { cfg.RequestTimeout = 10*time.Second + time.Nanosecond }, "(0,10s]"},
		{"response above cap", func(cfg *DeliveryLedgerReaderConfig) { cfg.MaxResponseBytes = maxLedgerSeedResponseBytes + 1 }, "response bytes"},
	}
	for _, test := range mutations {
		t.Run(test.name, func(t *testing.T) {
			cfg := valid
			test.edit(&cfg)
			_, err := NewDeliveryLedgerCheckpointReader(cfg)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error=%v want substring %q", err, test.want)
			}
		})
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		http.Error(writer, "ledger denied", http.StatusForbidden)
	}))
	reader, err := NewDeliveryLedgerCheckpointReader(DeliveryLedgerReaderConfig{
		URL: server.URL, Database: "catalog_dev", Username: "reader",
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = reader.Load(context.Background())
	server.Close()
	if err == nil || !strings.Contains(err.Error(), "HTTP 403: ledger denied") {
		t.Fatalf("HTTP error=%v", err)
	}

	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = reader.Load(canceled)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled error=%v", err)
	}
}

func TestDeliveryLedgerReaderRejectsUnknownResponseColumnsAndOversize(t *testing.T) {
	tests := []struct {
		name string
		body string
		max  int64
		want string
	}{
		{"unknown column", `{"project_id":"x","unexpected":"spans"}` + "\n", 1024, "unknown field"},
		{"oversize", strings.Repeat("x", 33), 32, "32-byte limit"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(writer, test.body)
			}))
			defer server.Close()
			reader, err := NewDeliveryLedgerCheckpointReader(DeliveryLedgerReaderConfig{
				URL: server.URL, Database: "catalog_dev", Username: "reader", MaxResponseBytes: test.max,
			})
			if err != nil {
				t.Fatal(err)
			}
			_, err = reader.Load(context.Background())
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("error=%v want substring %q", err, test.want)
			}
		})
	}
}

func TestDeliveryLedgerReaderAppliesHardContextDeadline(t *testing.T) {
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, request *http.Request) {
		select {
		case <-request.Context().Done():
		case <-release:
		}
	}))
	defer server.Close()
	defer close(release)
	reader, err := NewDeliveryLedgerCheckpointReader(DeliveryLedgerReaderConfig{
		URL: server.URL, Database: "catalog_dev", Username: "reader",
		RequestTimeout: 25 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	_, err = reader.Load(context.Background())
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline error=%v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("hard ledger deadline took %s", elapsed)
	}
}
