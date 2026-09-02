package propertycatalog

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

type recordingSink struct {
	calls      []string
	rows       [][]map[string]any
	failDataAt int
	failLedger bool
}

type recordingLeaseGuard struct {
	requests    []DeliveryLeaseRequest
	failAt      int
	onCall      func(int, DeliveryLeaseRequest)
	digests     []string
	role        string
	roles       []string
	projectIDs  []string
	spanSinceUS uint64
	spanUntilUS uint64
}

func (g *recordingLeaseGuard) AuthorizeDelivery(
	_ context.Context, request DeliveryLeaseRequest,
) (DeliveryLeaseEvidence, error) {
	g.requests = append(g.requests, request)
	call := len(g.requests)
	if g.onCall != nil {
		g.onCall(call, request)
	}
	if g.failAt == call {
		return DeliveryLeaseEvidence{}, errors.New("lease rotated")
	}
	digest := testDigest("build-lease")
	if call <= len(g.digests) {
		digest = g.digests[call-1]
	}
	role := g.role
	if role == "" {
		role = "definitions"
	}
	if call <= len(g.roles) {
		role = g.roles[call-1]
	}
	spanSinceUS, spanUntilUS := g.spanSinceUS, g.spanUntilUS
	if spanSinceUS == 0 || spanUntilUS == 0 {
		spanSinceUS, spanUntilUS = testSpanWindow()
	}
	projectIDs := g.projectIDs
	if len(projectIDs) == 0 {
		projectIDs = []string{testProject, testProjectTwo}
	}
	return DeliveryLeaseEvidence{
		BuildLeaseSHA256: digest, StreamRole: role,
		ProjectIDs:  append([]string(nil), projectIDs...),
		SpanSinceUS: spanSinceUS, SpanUntilUS: spanUntilUS,
	}, nil
}

func (s *recordingSink) InsertPropertyCatalog(_ context.Context, table Table, rows []map[string]any) error {
	s.calls = append(s.calls, "data:"+string(table))
	s.rows = append(s.rows, rows)
	if s.failDataAt != 0 && len(s.calls) == s.failDataAt {
		return errors.New("data failed")
	}
	return nil
}

func (s *recordingSink) InsertPropertyCatalogDelivery(_ context.Context, rows []map[string]any) error {
	s.calls = append(s.calls, "ledger")
	s.rows = append(s.rows, rows)
	if s.failLedger {
		return errors.New("ledger failed")
	}
	return nil
}

func definitionDeliveryInput(t *testing.T, rowCount int) EnvelopeInput {
	t.Helper()
	definitions := make([]DefinitionRow, rowCount)
	for index := range definitions {
		definitions[index] = testDefinition()
	}
	payload, err := BuildPayload(
		definitions, nil, 1, MaxChunkBytes, uint64(rowCount), testDigest("definition-delivery"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, uint64(rowCount))
	input.Payload = payload
	return input
}

func valueDeliveryInput(t *testing.T, rowCount int) EnvelopeInput {
	t.Helper()
	values := make([]AttributeValueRow, rowCount)
	for index := range values {
		values[index] = testValue()
	}
	payload, err := BuildPayload(
		nil, values, 1, MaxChunkBytes, uint64(rowCount), testDigest("value-delivery"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, uint64(rowCount))
	input.Payload = payload
	return input
}

func hotValueDeliveryInput(t *testing.T, row AttributeValueRow) EnvelopeInput {
	t.Helper()
	payload, err := BuildPayload(
		nil, []AttributeValueRow{row}, 1, MaxChunkBytes, 1, testDigest("hot-value-delivery"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	return input
}

func sourceAuditDeliveryInput(t *testing.T) EnvelopeInput {
	t.Helper()
	payload, err := BuildPayload(nil, nil, 1, MaxChunkBytes, 7, testDigest("source-audit-delivery"))
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 7)
	input.Payload = payload
	return input
}

func TestDeliveryPrevalidatesThenWritesDataBeforeLedger(t *testing.T) {
	sink := &recordingSink{}
	guard := &recordingLeaseGuard{}
	handler, err := NewDeliveryHandler(sink, guard, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	handler.now = func() time.Time { return time.Date(2026, 8, 14, 1, 2, 3, 4000, time.UTC) }
	envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope: envelope, Transport: TransportKafka, KafkaPartition: 2, KafkaOffset: 3,
	}); err != nil {
		t.Fatal(err)
	}
	want := []string{"data:property_definition_catalog", "ledger"}
	if fmt.Sprint(sink.calls) != fmt.Sprint(want) {
		t.Fatalf("delivery order=%v want=%v", sink.calls, want)
	}
	if len(guard.requests) != 2 {
		t.Fatalf("lease guard calls=%d want=2", len(guard.requests))
	}
	for _, request := range guard.requests {
		if request.OrganizationID != testOrganization || request.WorkspaceID != testWorkspace ||
			request.CatalogEpoch != 3 || request.CatalogRevision != 1 || request.BuildToken != testBuildToken ||
			request.ProjectionVersion != 1 || request.SourceAdapter != AdapterSpanAttribute ||
			request.ProducerStreamID != testStream || request.EnvelopeVersion != EnvelopeVersion ||
			request.Sequence != 1 || request.Terminal {
			t.Fatalf("guard request=%+v", request)
		}
	}
	if len(sink.rows[0][0]) != 38 {
		t.Fatalf("definition row shape=%d", len(sink.rows[0][0]))
	}
	ledger := sink.rows[1][0]
	if ledger["source_rows"] != uint64(1) || ledger["definition_rows"] != uint64(1) ||
		ledger["value_rows"] != uint64(0) || ledger["tombstone_rows"] != uint64(0) {
		t.Fatalf("ledger counts=%v", ledger)
	}
	wantLedgerColumns := map[string]struct{}{}
	for _, column := range []string{
		"organization_id", "workspace_id", "catalog_epoch", "catalog_revision", "build_token", "projection_version",
		"source_adapter", "producer_stream_id", "sequence", "terminal", "envelope_format", "envelope_version",
		"envelope_id", "payload_sha256", "previous_payload_sha256", "source_batch_digest",
		"outcome", "gap_reasons", "source_rows", "definition_rows", "value_rows", "tombstone_rows",
		"transport", "kafka_partition", "kafka_offset", "delivered_at", "_version",
	} {
		wantLedgerColumns[column] = struct{}{}
	}
	if len(ledger) != len(wantLedgerColumns) {
		t.Fatalf("ledger column count=%d want=%d: %v", len(ledger), len(wantLedgerColumns), ledger)
	}
	for column := range ledger {
		if _, exists := wantLedgerColumns[column]; !exists {
			t.Fatalf("ledger contains non-schema column %q: %v", column, ledger)
		}
	}
}

func TestDeliveryExactDuplicateSkipsLeaseDataAndLedgerWrites(t *testing.T) {
	sink := &recordingSink{}
	guard := &recordingLeaseGuard{failAt: 1}
	handler, _ := NewDeliveryHandler(sink, guard, time.Second)
	err := handler.Deliver(context.Background(), Delivery{
		Envelope:       mustEnvelope(t, definitionDeliveryInput(t, 1)),
		ExactDuplicate: true, Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 9,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(sink.calls) != 0 || len(guard.requests) != 0 {
		t.Fatalf("exact duplicate required live lease or refreshed durable rows: guard=%d writes=%v", len(guard.requests), sink.calls)
	}
}

func TestDeliveryNeverWritesLedgerBeforeAllData(t *testing.T) {
	sink := &recordingSink{failDataAt: 2}
	handler, _ := NewDeliveryHandler(sink, &recordingLeaseGuard{}, time.Second)
	err := handler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 2)),
		Transport: TransportDirect, KafkaPartition: -1, KafkaOffset: -1,
	})
	if err == nil || !strings.Contains(err.Error(), "data failed") {
		t.Fatalf("data failure=%v", err)
	}
	if fmt.Sprint(sink.calls) != "[data:property_definition_catalog data:property_definition_catalog]" {
		t.Fatalf("ledger written after partial failure: %v", sink.calls)
	}

	sink = &recordingSink{failLedger: true}
	handler, _ = NewDeliveryHandler(sink, &recordingLeaseGuard{}, time.Second)
	err = handler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 2)),
		Transport: TransportDirect, KafkaPartition: -1, KafkaOffset: -1,
	})
	if err == nil || !strings.Contains(err.Error(), "ledger failed") {
		t.Fatalf("ledger failure=%v", err)
	}
}

func TestDeliveryReauthorizesAtEveryWriteAndNeverCommitsPartialLedger(t *testing.T) {
	for _, test := range []struct {
		name      string
		failAt    int
		wantCalls string
	}{
		{name: "fence rotates between chunks", failAt: 2, wantCalls: "[data:property_definition_catalog]"},
		{name: "fence rotates before ledger", failAt: 3, wantCalls: "[data:property_definition_catalog data:property_definition_catalog]"},
	} {
		t.Run(test.name, func(t *testing.T) {
			sink := &recordingSink{}
			guard := &recordingLeaseGuard{failAt: test.failAt}
			handler, err := NewDeliveryHandler(sink, guard, time.Second)
			if err != nil {
				t.Fatal(err)
			}
			err = handler.Deliver(context.Background(), Delivery{
				Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 2)),
				Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 1,
			})
			if err == nil || !strings.Contains(err.Error(), "lease rotated") {
				t.Fatalf("rotation error=%v", err)
			}
			if got := fmt.Sprint(sink.calls); got != test.wantCalls {
				t.Fatalf("writes after rotation=%s want=%s", got, test.wantCalls)
			}
			for _, call := range sink.calls {
				if call == "ledger" {
					t.Fatalf("partial delivery was committed: %v", sink.calls)
				}
			}
		})
	}

	sink := &recordingSink{}
	guard := &recordingLeaseGuard{digests: []string{testDigest("lease-a"), testDigest("lease-b")}}
	handler, _ := NewDeliveryHandler(sink, guard, time.Second)
	err := handler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 2)),
		Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 1,
	})
	if err == nil || !strings.Contains(err.Error(), "build lease or stream role changed") ||
		fmt.Sprint(sink.calls) != "[data:property_definition_catalog]" {
		t.Fatalf("mid-delivery build lease rotation calls=%v err=%v", sink.calls, err)
	}

	sink = &recordingSink{}
	guard = &recordingLeaseGuard{roles: []string{"definitions", "hot_values"}}
	handler, _ = NewDeliveryHandler(sink, guard, time.Second)
	err = handler.Deliver(context.Background(), Delivery{
		Envelope:  mustEnvelope(t, definitionDeliveryInput(t, 2)),
		Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 1,
	})
	if err == nil || !strings.Contains(err.Error(), "build lease or stream role changed") ||
		fmt.Sprint(sink.calls) != "[data:property_definition_catalog]" {
		t.Fatalf("mid-delivery stream-role rotation calls=%v err=%v", sink.calls, err)
	}
}

func TestDeliveryRejectsCrossRolePayloadInjectionBeforeFirstWrite(t *testing.T) {
	for _, test := range []struct {
		name  string
		role  string
		input func(*testing.T) EnvelopeInput
	}{
		{name: "definitions cannot write values", role: "definitions", input: func(t *testing.T) EnvelopeInput {
			return valueDeliveryInput(t, 1)
		}},
		{name: "values cannot write definitions", role: "values", input: func(t *testing.T) EnvelopeInput {
			return definitionDeliveryInput(t, 1)
		}},
		{name: "hot values cannot write definitions", role: "hot_values", input: func(t *testing.T) EnvelopeInput {
			return definitionDeliveryInput(t, 1)
		}},
		{name: "source audit cannot write values", role: "source_audit", input: func(t *testing.T) EnvelopeInput {
			return valueDeliveryInput(t, 1)
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			sink := &recordingSink{}
			handler, _ := NewDeliveryHandler(sink, &recordingLeaseGuard{role: test.role}, time.Second)
			err := handler.Deliver(context.Background(), Delivery{
				Envelope: mustEnvelope(t, test.input(t)), Transport: TransportKafka,
				KafkaPartition: 0, KafkaOffset: 1,
			})
			if err == nil || len(sink.calls) != 0 {
				t.Fatalf("cross-role payload reached sink: calls=%v err=%v", sink.calls, err)
			}
		})
	}
}

func TestDeliveryHotValuesEnforcesBuildPlanProjectAndHalfOpenWindow(t *testing.T) {
	spanSinceUS, spanUntilUS := testSpanWindow()
	insideUntil := time.UnixMicro(int64(spanUntilUS - 1)).UTC().Format(dateTime64Layout)
	outsideProject := "99999999-9999-4999-8999-999999999999"
	for _, test := range []struct {
		name      string
		mutate    func(*AttributeValueRow)
		wantError bool
	}{
		{name: "inclusive lower and exclusive upper minus one"},
		{
			name:      "project outside source scope",
			mutate:    func(row *AttributeValueRow) { row.ProjectID = outsideProject },
			wantError: true,
		},
		{
			name: "before inclusive lower",
			mutate: func(row *AttributeValueRow) {
				row.FirstSeen = time.UnixMicro(int64(spanSinceUS - 1)).UTC().Format(dateTime64Layout)
			},
			wantError: true,
		},
		{
			name: "at exclusive upper",
			mutate: func(row *AttributeValueRow) {
				row.FirstSeen = insideUntil
				row.LastSeen = time.UnixMicro(int64(spanUntilUS)).UTC().Format(dateTime64Layout)
			},
			wantError: true,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			row := testValue()
			row.FirstSeen = time.UnixMicro(int64(spanSinceUS)).UTC().Format(dateTime64Layout)
			row.LastSeen = insideUntil
			if test.mutate != nil {
				test.mutate(&row)
			}
			sink := &recordingSink{}
			handler, err := NewDeliveryHandler(
				sink, &recordingLeaseGuard{role: "hot_values"}, time.Second,
			)
			if err != nil {
				t.Fatal(err)
			}
			err = handler.Deliver(context.Background(), Delivery{
				Envelope:  mustEnvelope(t, hotValueDeliveryInput(t, row)),
				Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 1,
			})
			if test.wantError {
				if err == nil || len(sink.calls) != 0 {
					t.Fatalf("out-of-scope hot value reached sink: calls=%v err=%v", sink.calls, err)
				}
				return
			}
			if err != nil || fmt.Sprint(sink.calls) != "[data:span_attribute_value_catalog ledger]" {
				t.Fatalf("in-scope boundary delivery calls=%v err=%v", sink.calls, err)
			}
		})
	}
}

func TestDeliveryAllowsSourceAuditEvidenceWithoutDataChunks(t *testing.T) {
	sink := &recordingSink{}
	guard := &recordingLeaseGuard{role: "source_audit"}
	handler, _ := NewDeliveryHandler(sink, guard, time.Second)
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope: mustEnvelope(t, sourceAuditDeliveryInput(t)), Transport: TransportReconcile,
		KafkaPartition: -1, KafkaOffset: -1,
	}); err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(sink.calls) != "[ledger]" || len(guard.requests) != 1 {
		t.Fatalf("source-audit evidence delivery guard=%d calls=%v", len(guard.requests), sink.calls)
	}
	ledger := sink.rows[0][0]
	if ledger["source_rows"] != uint64(7) || ledger["definition_rows"] != uint64(0) ||
		ledger["value_rows"] != uint64(0) || ledger["tombstone_rows"] != uint64(0) {
		t.Fatalf("source-audit ledger counts=%v", ledger)
	}
}
