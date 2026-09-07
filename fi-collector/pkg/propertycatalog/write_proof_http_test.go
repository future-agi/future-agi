package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func proofJSONResponse(t *testing.T, rows ...any) *http.Response {
	t.Helper()
	var body bytes.Buffer
	for _, row := range rows {
		if err := json.NewEncoder(&body).Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(bytes.NewReader(body.Bytes())), ContentLength: int64(body.Len())}
}

// This is a fake server transport, not a fake proof. The production HTTP proof
// parses all responses, validates direct identities/settings and checks rows.
type proofHTTPServer struct {
	t           *testing.T
	doc         WriteAdmission
	inserts     int
	loseACK     bool
	settled     bool
	lag         bool
	lagMember   string
	drift       bool
	queries     []string
	queryID     string
	quorum      string
	insertSQL   string
	extraGrants map[string][]string
}

func (s *proofHTTPServer) RoundTrip(r *http.Request) (*http.Response, error) {
	q := r.URL.Query()
	if strings.HasPrefix(q.Get("query"), "INSERT INTO ") {
		s.inserts++
		s.queryID, s.quorum = q.Get("query_id"), q.Get("insert_quorum")
		s.insertSQL = q.Get("query")
		if s.queryID == "" || q.Get("async_insert") != "0" || q.Get("wait_end_of_query") != "1" || q.Get("insert_quorum_parallel") != "1" {
			s.t.Fatalf("unpinned exact INSERT: %v", q)
		}
		if s.loseACK {
			s.loseACK = false
			return nil, io.ErrUnexpectedEOF
		}
		return proofJSONResponse(s.t), nil
	}
	body, _ := io.ReadAll(r.Body)
	statement := string(body)
	s.queries = append(s.queries, statement)
	if q.Get("database") != s.doc.Database || q.Get("wait_end_of_query") != "1" || q.Get("use_query_cache") != "0" {
		s.t.Fatalf("unbounded proof transport: %v", q)
	}
	var member *WriteMember
	for i := range s.doc.Members {
		if s.doc.Members[i].URL == r.URL.Scheme+"://"+r.URL.Host {
			member = &s.doc.Members[i]
		}
	}
	if member == nil {
		s.t.Fatalf("proof sent to unadmitted origin: %s", r.URL)
	}
	user, _, _ := r.BasicAuth()
	switch {
	case statement == writeTopologyQuery:
		rows := []any{}
		for _, table := range member.Tables {
			n := uint64(0)
			if s.doc.Family == "replicated" {
				n = uint64(len(s.doc.Members))
			}
			row := proofTopologyRow{Hostname: member.Hostname, ServerUUID: member.ServerUUID, Database: s.doc.Database, DatabaseUUID: member.DatabaseUUID,
				DatabaseEngine: "Atomic", Username: user, Name: table.Name, UUID: table.UUID, Engine: table.Engine, CreateSHA256: table.CreateSHA256,
				KeeperPath: table.KeeperPath, ReplicaName: table.ReplicaName, ReplicaNames: table.ReplicaNames, TotalReplicas: n, ActiveReplicas: n}
			if n > 0 {
				row.KeeperName = "default"
			}
			if s.drift {
				row.Hostname = "load-balancer-other-node"
			}
			rows = append(rows, row)
		}
		return proofJSONResponse(s.t, rows...), nil
	case statement == "SHOW GRANTS FORMAT JSONEachRow":
		rows := []any{}
		if user == "writer" {
			for _, table := range []string{string(DefinitionTable), string(AttributeValueTable), "property_catalog_deliveries"} {
				rows = append(rows, map[string]string{"GRANTS": "GRANT INSERT ON " + s.doc.Database + "." + table + " TO writer"})
			}
		} else {
			for _, table := range member.Tables {
				rows = append(rows, map[string]string{"GRANTS": "GRANT SELECT ON " + s.doc.Database + "." + table.Name + " TO reader"})
			}
		}
		for _, grant := range s.extraGrants[user] {
			rows = append(rows, map[string]string{"GRANTS": grant})
		}
		return proofJSONResponse(s.t, rows...), nil
	case statement == keeperSessionQuery:
		for i, m := range s.doc.Members {
			if m.Name == member.Name {
				return proofJSONResponse(s.t, keeperSession{Hostname: m.Hostname, ServerUUID: m.ServerUUID, Name: "default", ClientID: int64(i + 100)}), nil
			}
		}
	case strings.Contains(statement, "FROM system.zookeeper "):
		rows := []any{}
		for _, table := range member.Tables {
			for i, m := range s.doc.Members {
				rows = append(rows, keeperActive{Path: table.KeeperPath + "/replicas/" + m.Name, Name: "is_active", Value: "UUID_'" + m.ServerUUID + "'", Owner: int64(i + 100), CreateZXID: 1000})
			}
		}
		return proofJSONResponse(s.t, rows...), nil
	case strings.HasPrefix(statement, "SELECT hostName() AS hostname,toString(serverUUID())"):
		return proofJSONResponse(s.t, map[string]string{"hostname": member.Hostname, "server_uuid": member.ServerUUID, "database": s.doc.Database, "username": user}), nil
	case strings.Contains(statement, "FROM system.query_log"):
		if !s.settled {
			return proofJSONResponse(s.t), nil
		}
		return proofJSONResponse(s.t, map[string]any{"query_id": q.Get("param_attempt_id"), "type": "QueryFinish", "exception_code": 0,
			"current_database": s.doc.Database, "query": s.insertSQL, "async_insert": "0", "insert_quorum": s.quorum, "insert_quorum_parallel": "1"}), nil
	case strings.HasPrefix(statement, "SELECT toUInt8("):
		got := 1
		if s.lag || s.lagMember == member.Name {
			got = 0
		}
		return proofJSONResponse(s.t, map[string]int{"r0": got}), nil
	default:
		s.t.Fatalf("unexpected real proof SQL: %s", statement)
		return nil, errors.New("unexpected query")
	}
	return nil, errors.New("unexpected proof query")
}

func proofHTTPFixture(t *testing.T, n int) (*HTTPWriteProof, *proofHTTPServer) {
	t.Helper()
	doc := testWriteAdmission(t, n, false)
	policy, err := NewWritePolicy(doc)
	if err != nil {
		t.Fatal(err)
	}
	server := &proofHTTPServer{t: t, doc: doc}
	proof, err := NewHTTPWriteProof(ClickHouseSinkConfig{URL: doc.Members[0].URL, Database: doc.Database, Environment: doc.Environment,
		Username: "reader", RoundTripper: server}, policy)
	if err != nil {
		t.Fatal(err)
	}
	return proof, server
}

func TestHTTPWriteProofClustersGrantIsExactSelectOnly(t *testing.T) {
	for _, test := range []struct {
		name    string
		grant   string
		allowed bool
	}{
		{"exact-select", "GRANT SELECT ON system.clusters TO reader", true},
		{"quoted-select", "GRANT SELECT ON `system`.`clusters` TO `reader`", true},
		{"system-wildcard", "GRANT SELECT ON system.* TO reader", false},
		{"global-wildcard", "GRANT SELECT ON *.* TO reader", false},
		{"insert", "GRANT INSERT ON system.clusters TO reader", false},
		{"combined-privileges", "GRANT SELECT, INSERT ON system.clusters TO reader", false},
		{"other-table", "GRANT SELECT ON system.clusters_extra TO reader", false},
		{"foreign-database", "GRANT SELECT ON foreign.clusters TO reader", false},
		{"foreign-grantee", "GRANT SELECT ON system.clusters TO other_reader", false},
		{"multiple-grantees", "GRANT SELECT ON system.clusters TO other_reader, reader", false},
		{"grant-option", "GRANT SELECT ON system.clusters TO reader WITH GRANT OPTION", false},
		{"role", "GRANT discovery_role TO reader", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			proof, server := proofHTTPFixture(t, 1)
			if got := safeProofGrant(test.grant, server.doc.Database, "reader"); got != test.allowed {
				t.Fatalf("safeProofGrant=%v want %v", got, test.allowed)
			}
			server.extraGrants = map[string][]string{"reader": {test.grant}}
			err := proof.Attest(boundedWriteTestContext(t), proof.policy)
			if (err == nil) != test.allowed {
				t.Fatalf("grant attestation error=%v want allowed=%v", err, test.allowed)
			}
			if err != nil && !strings.Contains(err.Error(), "proof principal has unreviewed grants") {
				t.Fatalf("unexpected rejection: %v", err)
			}
			if server.inserts != 0 {
				t.Fatal("grant attestation performed an INSERT")
			}
		})
	}
}

func TestHTTPWriteProofClustersGrantDoesNotWidenWriterPrivileges(t *testing.T) {
	for _, n := range []int{1, 2, 3} {
		t.Run(fmt.Sprintf("N%d", n), func(t *testing.T) {
			proof, server := proofHTTPFixture(t, n)
			server.extraGrants = map[string][]string{"reader": {"GRANT SELECT ON system.clusters TO reader"}}
			writer, err := newClickHouseTransport(ClickHouseSinkConfig{URL: server.doc.Members[0].URL,
				Database: server.doc.Database, Environment: server.doc.Environment, Username: "writer", RoundTripper: server})
			if err != nil {
				t.Fatal(err)
			}
			proof.writer = writer
			if err := proof.Attest(boundedWriteTestContext(t), proof.policy); err != nil {
				t.Fatalf("shared discovery SELECT with exact writer grants rejected: %v", err)
			}
			for _, grant := range []string{"GRANT SELECT ON system.clusters TO writer", "GRANT discovery_role TO writer", "GRANT INSERT ON system.clusters TO writer"} {
				server.extraGrants["writer"] = []string{grant}
				if err := proof.Attest(boundedWriteTestContext(t), proof.policy); err == nil {
					t.Fatalf("writer acquired unapproved discovery privilege: %s", grant)
				}
			}
			if server.inserts != 0 {
				t.Fatal("grant attestation performed an INSERT")
			}
		})
	}
}

func TestHTTPWriteProofDirectIdentityAndCoverageAreMandatory(t *testing.T) {
	for _, n := range []int{1, 2, 3, 4} {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			proof, server := proofHTTPFixture(t, n)
			ctx := boundedWriteTestContext(t)
			if err := proof.Attest(ctx, proof.policy); err != nil {
				t.Fatal(err)
			}
			server.drift = true
			if err := proof.Attest(ctx, proof.policy); err == nil {
				t.Fatal("HTTP route substitution accepted")
			}
			server.drift = false
			envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
			attempt := ExactWriteAttempt{Body: envelope.Snapshot().Payload.Chunks[0].JSONEachRow, Table: string(DefinitionTable), TopologySHA256: server.doc.TopologySHA256}
			if err := proof.Cover(ctx, proof.policy, attempt); err != nil {
				t.Fatal(err)
			}
			server.lagMember = server.doc.Members[len(server.doc.Members)-1].Name
			if err := proof.Cover(ctx, proof.policy, attempt); err == nil {
				t.Fatal("missing replica rows accepted")
			}
			if server.inserts != 0 {
				t.Fatal("proof path performed writes")
			}
		})
	}
}

func TestHTTPWriteProofRejectsIncompleteDuplicateAndExceptionResponses(t *testing.T) {
	for _, body := range []string{"{\"ok\":1}", "{\"ok\":1,\"ok\":1}\n", "{\"v\":{\"k\":0,\"k\":1}}\n", "{\"ok\":1}\nCode: 242. DB::Exception\n", "null\n", "[]\n", strings.Repeat(" ", 4097)} {
		t.Run(fmt.Sprintf("%x", sha256Hex([]byte(body))[:8]), func(t *testing.T) {
			proof, _ := proofHTTPFixture(t, 1)
			proof.reader.client.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}, nil
			})
			if _, err := proof.query(boundedWriteTestContext(t), proof.policy.admission.Members[0].URL, "SELECT 1", nil, 4096); err == nil {
				t.Fatal("ambiguous proof response accepted")
			}
		})
	}
	proof, _ := proofHTTPFixture(t, 1)
	proof.reader.client.Transport = roundTripFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(io.MultiReader(strings.NewReader("{\"ok\":1}\n"), insertAckErrorReader{io.ErrUnexpectedEOF}))}, nil
	})
	if rows, err := proof.query(boundedWriteTestContext(t), proof.policy.admission.Members[0].URL, "SELECT 1", nil, 4096); err == nil || rows != nil {
		t.Fatal("partial proof rows escaped before failed EOF")
	}
}

func TestHTTPWriteProofReadDisagreementAndBoundsAreNotLost(t *testing.T) {
	proof, server := proofHTTPFixture(t, 2)
	original := proof.reader.client.Transport
	proof.reader.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		body, _ := io.ReadAll(r.Body)
		r.Body = io.NopCloser(bytes.NewReader(body))
		if string(body) == "SELECT test_inventory" {
			if r.URL.Query().Get("max_rows_to_group_by") != "100001" || r.URL.Query().Get("max_result_rows") != "2" {
				t.Fatal("checkpoint bounds dropped")
			}
			return proofJSONResponse(t, map[string]string{"node": r.URL.Host}), nil
		}
		return original.RoundTrip(r)
	})
	_, err := proof.readAgreedBounded(boundedWriteTestContext(t), "SELECT test_inventory", nil, map[string]string{
		"max_rows_to_group_by": "100001", "group_by_overflow_mode": "throw", "max_result_rows": "2", "max_execution_time": "10"}, 4096)
	if err == nil || !strings.Contains(err.Error(), "disagree") || server.inserts != 0 {
		t.Fatalf("disagreement result=%v", err)
	}
	for _, settings := range []map[string]string{{"database": "foreign"}, {"use_query_cache": "1"}, {"max_execution_time": "0"}, {"group_by_overflow_mode": "any"}} {
		if _, err := proof.queryBounded(boundedWriteTestContext(t), server.doc.Members[0].URL, "SELECT 1", nil, settings, 4096); err == nil {
			t.Fatal("unsafe proof setting accepted")
		}
	}
}

func TestHTTPWriteProofRealConsumerLostACKRestartAndDuplicateBoundary(t *testing.T) {
	proof, server := proofHTTPFixture(t, 1)
	directory := t.TempDir()
	identityRaw := alteredInstallationIdentity(t, func(d map[string]any) { d["target_database"] = server.doc.Database })
	var identity installationIdentityDocument
	if err := json.Unmarshal(identityRaw, &identity); err != nil {
		t.Fatal(err)
	}
	server.doc.InstallationSHA256 = identity.IdentitySHA256
	server.doc.TopologySHA256 = admissionDigest(server.doc)
	admissionRaw, _ := json.Marshal(server.doc)
	for name, raw := range map[string][]byte{InstallationIdentityFilename: identityRaw, WriteAdmissionFilename: append(admissionRaw, '\n')} {
		if err := os.WriteFile(filepath.Join(directory, name), raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	makeSink := func() (*ClickHouseSink, *WriteAttemptJournal) {
		write := ClickHouseSinkConfig{URL: "http://service.invalid:8123", Database: server.doc.Database, Environment: server.doc.Environment,
			Username: "writer", RoundTripper: server, InstallationDirectory: directory, OrderedTopic: identity.OrderedTopic}
		read := write
		read.Username = "reader"
		sink, _, journal, err := ConfigureDurableClickHouse(write, read)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { journal.Close() })
		return sink, journal
	}
	sink, journal := makeSink()
	server.loseACK = true
	envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
	source := &oneRecordSource{record: kafkaRecord(t, envelope, 7)}
	validator, _ := NewSequenceValidator(nil)
	makeConsumer := func(sink *ClickHouseSink) *Consumer {
		handler, err := NewDeliveryHandler(sink, &recordingLeaseGuard{}, time.Second)
		if err != nil {
			t.Fatal(err)
		}
		consumer, err := NewConsumer("property-catalog", source, handler, validator)
		if err != nil {
			t.Fatal(err)
		}
		return consumer
	}
	consumer := makeConsumer(sink)
	if err := consumer.ProcessOne(context.Background()); !errors.Is(err, ErrWriteUnresolved) || source.commits != 0 {
		t.Fatalf("lost ACK=%v commits=%d", err, source.commits)
	}
	journal.Close()
	sink, _ = makeSink()
	consumer = makeConsumer(sink)
	if err := consumer.ProcessOne(context.Background()); !errors.Is(err, ErrWriteUnresolved) || source.commits != 0 || server.inserts != 1 {
		t.Fatalf("restart replay=%v commits=%d inserts=%d", err, source.commits, server.inserts)
	}
	server.settled = true
	if err := consumer.ProcessOne(context.Background()); err != nil || source.commits != 1 || server.inserts != 2 {
		t.Fatalf("positive settlement=%v commits=%d inserts=%d", err, source.commits, server.inserts)
	}
	server.lag = true
	if err := consumer.ProcessOne(context.Background()); err == nil || source.commits != 1 || server.inserts != 2 {
		t.Fatalf("duplicate bypass=%v commits=%d inserts=%d", err, source.commits, server.inserts)
	}
	_ = proof
}

func TestHTTPWriteProofValueCoverageHandlesAggregationWithoutPhysicalCountEquality(t *testing.T) {
	envelope := mustEnvelope(t, valueDeliveryInput(t, 1))
	rows, err := decodeExactWriteRows(envelope.Snapshot().Payload.Chunks[0].JSONEachRow, string(AttributeValueTable))
	if err != nil {
		t.Fatal(err)
	}
	query, err := coverageQuery(string(AttributeValueTable), rows)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"minIf(first_seen,", "maxIf(last_seen,", "value_json!=", "value_search_text_folded!=", "countIf("} {
		if !strings.Contains(query, want) {
			t.Fatalf("coverage omits aggregate invariant %q", want)
		}
	}
}
