package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

func testWriteAdmission(t *testing.T, n int, production bool) WriteAdmission {
	t.Helper()
	doc := WriteAdmission{
		Database: "property_catalog_dev_sink_test", Environment: DevelopmentEnvironment,
		Family: "standalone", Format: "futureagi.property-catalog-write-admission", Version: 1,
		InstallationSHA256: testDigest("installation"),
	}
	if production {
		doc.Environment, doc.Database = ProductionEnvironment, "property_catalog"
	}
	names := make([]string, n)
	for i := range names {
		names[i] = fmt.Sprintf("replica%02d", i+1)
	}
	if n > 1 {
		doc.Family, doc.KeeperIdentitySHA256 = "replicated", testDigest("one keeper ensemble")
	}
	tables := make([]string, 0, len(catalogEngineFamilies))
	for name := range catalogEngineFamilies {
		tables = append(tables, name)
	}
	sort.Strings(tables)
	for i, name := range names {
		member := WriteMember{
			Name: name, Hostname: name, URL: "http://" + name + ":8123",
			DatabaseUUID: fmt.Sprintf("11111111-1111-4111-8111-%012d", i+1),
			ServerUUID:   fmt.Sprintf("33333333-3333-4333-8333-%012d", i+1),
		}
		for j, name := range tables {
			table := WriteTable{
				Name: name, Engine: catalogEngineFamilies[name], CreateSHA256: testDigest(name),
				UUID: fmt.Sprintf("22222222-2222-4222-8222-%012d", (i+1)*10+j), ReplicaNames: []string{},
			}
			if n > 1 {
				table.Engine = "Replicated" + table.Engine
				table.KeeperPath = "/clickhouse/tables/" + doc.Database + "/1/" + name
				table.ReplicaName, table.ReplicaNames = member.Name, slices.Clone(names)
			}
			member.Tables = append(member.Tables, table)
		}
		doc.Members = append(doc.Members, member)
	}
	if n > 1 {
		doc.KeeperIdentitySHA256 = keeperMembershipDigest(doc)
	}
	doc.TopologySHA256 = admissionDigest(doc)
	return doc
}

func TestWritePolicyTopologySelectsAllMembersNotEnvironmentOrMajority(t *testing.T) {
	for _, n := range []int{1, 2, 3, 4, 8} {
		t.Run(strconv.Itoa(n), func(t *testing.T) {
			doc := testWriteAdmission(t, n, false)
			policy, err := NewWritePolicy(doc)
			if err != nil {
				t.Fatal(err)
			}
			doc.Members[0].Tables[0].ReplicaNames = nil
			doc.Members = doc.Members[:1] // caller cannot shrink the admitted quorum
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			settings, err := policy.InsertSettings(ctx)
			if err != nil {
				t.Fatal(err)
			}
			want := n
			if n == 1 {
				want = 0
			}
			if settings["insert_quorum"] != strconv.Itoa(want) || settings["insert_quorum_parallel"] != "1" ||
				settings["async_insert"] != "0" || settings["wait_end_of_query"] != "1" {
				t.Fatalf("unsafe settings: %v", settings)
			}
			for key := range settings {
				if strings.HasPrefix(key, "log_") {
					t.Fatal("logging must not be imposed as the sole correctness path")
				}
			}
		})
	}
}

func TestWritePolicyProductionRemainsExactlyThree(t *testing.T) {
	for _, n := range []int{1, 2, 3, 4} {
		_, err := NewWritePolicy(testWriteAdmission(t, n, true))
		if (err == nil) != (n == 3) {
			t.Fatalf("replicas=%d err=%v", n, err)
		}
	}
}

func TestWritePolicyRejectsIncompleteForeignAndConflictingAdmission(t *testing.T) {
	mutations := map[string]func(*WriteAdmission){
		"missing members":      func(d *WriteAdmission) { d.Members = nil },
		"duplicate endpoint":   func(d *WriteAdmission) { d.Members[1].URL = d.Members[0].URL },
		"duplicate host":       func(d *WriteAdmission) { d.Members[1].Hostname = d.Members[0].Hostname },
		"alias member":         func(d *WriteAdmission) { d.Members[1].Name = d.Members[0].Name },
		"missing keeper proof": func(d *WriteAdmission) { d.KeeperIdentitySHA256 = "" },
		"mixed engine":         func(d *WriteAdmission) { d.Members[0].Tables[0].Engine = "MergeTree" },
		"partial schema":       func(d *WriteAdmission) { d.Members[0].Tables = d.Members[0].Tables[:6] },
		"foreign table":        func(d *WriteAdmission) { d.Members[0].Tables[0].Name = "spans" },
		"foreign path":         func(d *WriteAdmission) { d.Members[1].Tables[0].KeeperPath += "other" },
		"active subset":        func(d *WriteAdmission) { d.Members[0].Tables[0].ReplicaNames = []string{d.Members[0].Name} },
		"foreign peer":         func(d *WriteAdmission) { d.Members[0].Tables[0].ReplicaNames[1] = "foreign" },
		"database uuid":        func(d *WriteAdmission) { d.Members[0].DatabaseUUID = "" },
		"missing server uuid":  func(d *WriteAdmission) { d.Members[0].ServerUUID = "" },
		"aliased server uuid":  func(d *WriteAdmission) { d.Members[1].ServerUUID = d.Members[0].ServerUUID },
		"embedded credentials": func(d *WriteAdmission) { d.Members[0].URL = "http://user:secret@host:8123" },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			doc := testWriteAdmission(t, 3, true)
			mutate(&doc)
			doc.TopologySHA256 = admissionDigest(doc)
			if _, err := NewWritePolicy(doc); err == nil {
				t.Fatal("invalid topology accepted")
			}
		})
	}
}

func TestWritePolicyCanonicalPythonJSONAndExplicitEmptyArrays(t *testing.T) {
	doc := testWriteAdmission(t, 1, false)
	raw, _ := json.Marshal(doc)
	// Python's compact sort_keys form has precisely this recursive key order.
	var fields map[string]any
	_ = json.Unmarshal(raw, &fields)
	pythonOrder, _ := json.Marshal(fields)
	if !bytes.Equal(raw, pythonOrder) || !bytes.Contains(raw, []byte(`"replica_names":[]`)) {
		t.Fatalf("not canonical sorted JSON with []: %s", raw)
	}
	if _, err := ParseWriteAdmission(append(raw, '\n')); err != nil {
		t.Fatal(err)
	}
	for _, bad := range [][]byte{
		bytes.ReplaceAll(raw, []byte(`"replica_names":[]`), []byte(`"replica_names":null`)),
		bytes.Replace(raw, []byte(`"database":`), []byte(`"Database":`), 1),
		bytes.Replace(raw, []byte(`"version":1`), []byte(`"version":1,"version":1`), 1),
		bytes.Replace(raw, []byte(`"replica_names":[],`), nil, 1),
	} {
		if _, err := ParseWriteAdmission(append(bad, '\n')); err == nil {
			t.Fatal("noncanonical or incomplete admission accepted")
		}
	}
}

func TestWritePolicyRequiresProofBoundDestinationAndDeadline(t *testing.T) {
	policy, _ := NewWritePolicy(testWriteAdmission(t, 1, false))
	if err := policy.RequireDestination(DevelopmentEnvironment, policy.admission.Database, "http://load-balancer:8123"); err == nil {
		t.Fatal("unadmitted endpoint accepted")
	}
	if _, err := policy.InsertSettings(context.Background()); err == nil {
		t.Fatal("unbounded write accepted")
	}
	var missing *WritePolicy
	if _, err := missing.InsertSettings(context.Background()); err == nil {
		t.Fatal("missing proof accepted")
	}
}
