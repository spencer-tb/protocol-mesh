package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
	"unsafe"
)

func TestFindLatestRuns_Basic(t *testing.T) {
	now := time.Now()
	runs := []TestRun{
		{Name: "sim/A", Clients: []string{"reth_default"}, Start: now},
		{Name: "sim/A", Clients: []string{"reth_default"}, Start: now.Add(-time.Hour)},
		{Name: "sim/B", Clients: []string{"besu_default"}, Start: now},
	}
	latest := findLatestRuns(runs)
	if len(latest) != 2 {
		t.Fatalf("expected 2 latest runs, got %d", len(latest))
	}
	// First run for sim/A (newest), first run for sim/B.
	if latest[0].Name != "sim/A" || latest[1].Name != "sim/B" {
		t.Errorf("unexpected order: %v, %v", latest[0].Name, latest[1].Name)
	}
}

func TestFindLatestRuns_MultiClientNoDuplicates(t *testing.T) {
	now := time.Now()
	runs := []TestRun{
		{Name: "sim/X", Clients: []string{"reth_default", "besu_default"}, Start: now},
		{Name: "sim/X", Clients: []string{"reth_default"}, Start: now.Add(-time.Hour)},
	}
	latest := findLatestRuns(runs)
	if len(latest) != 1 {
		t.Fatalf("expected 1 latest run (multi-client covers both), got %d", len(latest))
	}
}

func TestFindLatestRuns_SeparateClients(t *testing.T) {
	now := time.Now()
	runs := []TestRun{
		{Name: "sim/X", Clients: []string{"reth_default"}, Start: now},
		{Name: "sim/X", Clients: []string{"besu_default"}, Start: now.Add(-time.Minute)},
	}
	latest := findLatestRuns(runs)
	if len(latest) != 2 {
		t.Fatalf("expected 2 (one per client), got %d", len(latest))
	}
}

func TestFindLatestRuns_AllSeen(t *testing.T) {
	now := time.Now()
	runs := []TestRun{
		{Name: "sim/X", Clients: []string{"reth_default", "besu_default"}, Start: now},
		// Both clients already seen — should be skipped.
		{Name: "sim/X", Clients: []string{"reth_default", "besu_default"}, Start: now.Add(-time.Hour)},
	}
	latest := findLatestRuns(runs)
	if len(latest) != 1 {
		t.Fatalf("expected 1, got %d", len(latest))
	}
}

func TestFindLatestRuns_Empty(t *testing.T) {
	latest := findLatestRuns(nil)
	if len(latest) != 0 {
		t.Fatalf("expected 0, got %d", len(latest))
	}
}

// mockHiveServer returns an httptest.Server that mimics the hive static file server.
func mockHiveServer(t *testing.T) *httptest.Server {
	t.Helper()

	now := time.Now().UTC()

	listing := []TestRun{
		{
			Name: "eels/consume-engine", NTests: 10, Passes: 8, Fails: 2,
			Clients: []string{"reth_default"}, Versions: map[string]string{"reth_default": "Reth Version: 1.0.0+aabbccdd"},
			Start: now, FileName: "suite-001.json", Size: 1234,
		},
	}

	detail := SuiteResult{
		ID: 1, Name: "eels/consume-engine",
		TestCases: map[string]TestCase{
			"1": {
				Name:          "tests/osaka/test_eip.py::test_something[fork_Osaka-engine]-reth_default",
				Description:   `<a href="https://github.com/ethereum/tests/blob/main/test.py">[source]</a>`,
				SummaryResult: SummaryResult{Pass: false, Details: "assertion failed"},
			},
			"2": {
				Name:          "tests/osaka/test_eip.py::test_passing[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: true},
			},
		},
	}

	discovery := []map[string]string{{"name": "test-group"}}

	mux := http.NewServeMux()
	mux.HandleFunc("/discovery.json", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(discovery)
	})
	mux.HandleFunc("/test-group/listing.jsonl", func(w http.ResponseWriter, r *http.Request) {
		for _, run := range listing {
			data, _ := json.Marshal(run)
			fmt.Fprintln(w, string(data))
		}
	})
	mux.HandleFunc("/test-group/results/suite-001.json", func(w http.ResponseWriter, r *http.Request) {
		data, _ := json.Marshal(detail)
		http.ServeContent(w, r, "suite-001.json", time.Time{}, bytes.NewReader(data))
	})
	mux.HandleFunc("/test-group/results/sim.log", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "line1\nline2\nline3\nline4\nline5\n")
	})

	return httptest.NewServer(mux)
}

func TestStoreDiscoverGroups(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, nil)
	groups, err := store.discoverGroups()
	if err != nil {
		t.Fatalf("discoverGroups: %v", err)
	}
	if len(groups) != 1 || groups[0] != "test-group" {
		t.Errorf("got groups %v, want [test-group]", groups)
	}
}

func TestStoreFetchListing(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})
	runs, err := store.fetchListing("test-group")
	if err != nil {
		t.Fatalf("fetchListing: %v", err)
	}
	if len(runs) != 1 {
		t.Fatalf("expected 1 run, got %d", len(runs))
	}
	if runs[0].Name != "eels/consume-engine" {
		t.Errorf("unexpected run name: %s", runs[0].Name)
	}
}

func TestStoreGetSuite(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})

	suite, err := store.GetSuite("test-group", "suite-001.json", true)
	if err != nil {
		t.Fatalf("GetSuite: %v", err)
	}
	want := []SuiteTest{
		{
			Name:   "tests/osaka/test_eip.py::test_something[fork_Osaka-engine]",
			Source: "https://github.com/ethereum/tests/blob/main/test.py",
			ID:     1,
		},
		{
			Name: "tests/osaka/test_eip.py::test_passing[fork_Osaka-engine]",
			ID:   2,
			Pass: true,
		},
	}
	if len(suite.Tests) != len(want) {
		t.Fatalf("expected %d tests, got %d", len(want), len(suite.Tests))
	}
	for i, w := range want {
		got := suite.Tests[i]
		got.Offset, got.Size = 0, 0
		if got != w {
			t.Errorf("test %d = %+v, want %+v", i, got, w)
		}
	}

	// Second call should return cached.
	suite2, err := store.GetSuite("test-group", "suite-001.json", true)
	if err != nil {
		t.Fatalf("GetSuite (cached): %v", err)
	}
	if suite != suite2 {
		t.Error("expected same pointer from cache")
	}
}

func TestReadSuite_MetadataOnlyStopsAtRunMetadata(t *testing.T) {
	// Anything after the run metadata is never read, so it may be invalid.
	input := `{"id":1,"runMetadata":{"hiveCommand":["./hive"]},"testCases":{not json`
	suite, err := readSuite(strings.NewReader(input), false, newInterner().get)
	if err != nil {
		t.Fatalf("readSuite: %v", err)
	}
	if suite.HasTests || suite.RunMetadata == nil || suite.RunMetadata.HiveCommand[0] != "./hive" {
		t.Errorf("unexpected suite: %+v", suite)
	}
}

func TestReadSuite_SharesNamesAcrossClients(t *testing.T) {
	suiteFor := func(client string) string {
		return `{"testCases":{"7":{"name":"m.py::test_x[fork_Osaka]-` + client +
			`","summaryResult":{"pass":false}}},"testDetailsLog":"details/1.log"}`
	}
	names := newInterner()
	reth, err := readSuite(strings.NewReader(suiteFor("reth_default")), true, names.get)
	if err != nil {
		t.Fatalf("readSuite: %v", err)
	}
	besu, err := readSuite(strings.NewReader(suiteFor("besu_default")), true, names.get)
	if err != nil {
		t.Fatalf("readSuite: %v", err)
	}
	a, b := reth.Tests[0].Name, besu.Tests[0].Name
	if a != "m.py::test_x[fork_Osaka]" || unsafe.StringData(a) != unsafe.StringData(b) {
		t.Errorf("expected one shared name without the client suffix, got %q and %q", a, b)
	}
	if reth.TestDetailsLog != "details/1.log" || reth.Tests[0].ID != 7 {
		t.Errorf("unexpected suite: %+v", reth)
	}
}

func TestStoreGetTestCase(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})

	tc, _, err := store.GetTestCase("test-group", "suite-001.json", 1)
	if err != nil {
		t.Fatalf("GetTestCase: %v", err)
	}
	if tc.SummaryResult.Details != "assertion failed" || !strings.Contains(tc.Description, "[source]") {
		t.Errorf("unexpected test case: %+v", tc)
	}

	if _, _, err := store.GetTestCase("test-group", "suite-001.json", 99); err != errTestNotFound {
		t.Errorf("expected errTestNotFound, got %v", err)
	}
}

func TestStoreTestURL(t *testing.T) {
	store := NewStore("https://hive.ethpandaops.io/", nil)
	got := store.TestURL("heze", "1793284041-33608a1f.json", "4804")
	want := "https://hive.ethpandaops.io/#/test/heze/1793284041-33608a1f?testnumber=4804"
	if got != want {
		t.Errorf("TestURL = %q, want %q", got, want)
	}
}

func TestStoreFetchLog(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})

	// Full log.
	data, err := store.FetchLog("test-group", "sim.log", -1, -1)
	if err != nil {
		t.Fatalf("FetchLog: %v", err)
	}
	if !strings.Contains(data, "line1") {
		t.Error("expected log content")
	}
}

func TestStoreGetGroups(t *testing.T) {
	store := NewStore("http://example.com", []string{"a", "b"})
	groups := store.GetGroups()
	if len(groups) != 2 || groups[0] != "a" || groups[1] != "b" {
		t.Errorf("GetGroups = %v, want [a b]", groups)
	}

	// Mutating the returned slice should not affect the store.
	groups[0] = "modified"
	original := store.GetGroups()
	if original[0] != "a" {
		t.Error("GetGroups returned a reference instead of a copy")
	}
}

func TestStoreRefresh(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})
	store.refresh()

	runs, ok := store.GetLatestRuns("test-group")
	if !ok {
		t.Fatal("expected test-group in latest")
	}
	if len(runs) != 1 {
		t.Fatalf("expected 1 latest run, got %d", len(runs))
	}

	allRuns, ok := store.GetAllRuns("test-group")
	if !ok {
		t.Fatal("expected test-group in listings")
	}
	if len(allRuns) != 1 {
		t.Fatalf("expected 1 total run, got %d", len(allRuns))
	}
}

func TestStoreRefresh_EvictsStaleSuites(t *testing.T) {
	srv := mockHiveServer(t)
	defer srv.Close()

	store := NewStore(srv.URL, []string{"test-group"})

	// Pre-populate a stale suite entry.
	store.suitesMu.Lock()
	store.suites["test-group/old-file.json"] = &Suite{}
	store.suitesMu.Unlock()

	store.refresh()

	store.suitesMu.RLock()
	_, exists := store.suites["test-group/old-file.json"]
	_, loaded := store.suites["test-group/suite-001.json"]
	store.suitesMu.RUnlock()

	if exists {
		t.Error("stale suite entry should have been evicted")
	}
	if !loaded {
		t.Error("the latest run's suite should have been pre-fetched")
	}
}

func TestStoreDiscoverGroups_Error(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
	}))
	defer srv.Close()

	store := NewStore(srv.URL, nil)
	_, err := store.discoverGroups()
	if err == nil {
		t.Error("expected error for 500 response")
	}
}

func TestStoreFetchListing_Error(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
	}))
	defer srv.Close()

	store := NewStore(srv.URL, nil)
	_, err := store.fetchListing("nonexistent")
	if err == nil {
		t.Error("expected error for 404 response")
	}
}

func TestStoreGetSuite_Error(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
	}))
	defer srv.Close()

	store := NewStore(srv.URL, nil)
	_, err := store.GetSuite("group", "file.json", true)
	if err == nil {
		t.Error("expected error for 404 response")
	}
}
