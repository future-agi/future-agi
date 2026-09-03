package propertycatalog

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) { return f(request) }

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
		return &http.Response{
			StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader("")), Header: make(http.Header),
		}, nil
	})
	sink, err := NewClickHouseSink(ClickHouseSinkConfig{
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
		if _, err := NewClickHouseSink(cfg); err == nil {
			t.Fatalf("unsafe sink config accepted: %+v", cfg)
		}
	}
	called := false
	sink, err := NewClickHouseSink(ClickHouseSinkConfig{
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
	if _, err := NewClickHouseSink(ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "legacy_catalog_snapshot",
		Environment: DevelopmentEnvironment, Username: "property_writer",
	}); err != nil {
		t.Fatal(err)
	}
}

func TestClickHouseSinkAcceptsOnlyStableProductionCatalogName(t *testing.T) {
	if _, err := NewClickHouseSink(ClickHouseSinkConfig{
		URL: "https://clickhouse:8443", Database: "property_catalog",
		Environment: ProductionEnvironment, Username: "property_writer",
	}); err != nil {
		t.Fatal(err)
	}
}
