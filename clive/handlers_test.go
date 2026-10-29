package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// testFixtures returns a Store whose suite files a test server serves.
// The store has one group "tg" with two runs: one for reth (2 fails, 8 passes)
// and one for besu (1 fail, 9 passes), plus detail data.
func testFixtures(t *testing.T) *Store {
	now := time.Date(2026, 3, 1, 12, 0, 0, 0, time.UTC)
	earlier := now.Add(-24 * time.Hour)

	runs := []TestRun{
		{
			Name: "eels/consume-engine", NTests: 10, Passes: 8, Fails: 2,
			Clients:  []string{"reth_default"},
			Versions: map[string]string{"reth_default": "Reth Version: 1.0.0+aabbccdd"},
			Start:    now, FileName: "suite-reth.json",
		},
		{
			Name: "eels/consume-engine", NTests: 10, Passes: 9, Fails: 1,
			Clients:  []string{"besu_default"},
			Versions: map[string]string{"besu_default": "besu/v26.3-develop-f9b20c2/linux"},
			Start:    now, FileName: "suite-besu.json",
		},
	}

	// Older batch for diff testing.
	olderRuns := []TestRun{
		{
			Name: "eels/consume-engine", NTests: 10, Passes: 7, Fails: 3,
			Clients:  []string{"reth_default"},
			Versions: map[string]string{"reth_default": "Reth Version: 0.9.0+11223344"},
			Start:    earlier, FileName: "suite-reth-old.json",
		},
	}

	allRuns := append(runs, olderRuns...)

	rethDetail := &SuiteResult{
		ID: 1, Name: "eels/consume-engine",
		TestCases: map[string]TestCase{
			"1": {
				Name:          "tests/osaka/test_eip.py::test_fail_one[fork_Osaka-engine]-reth_default",
				Description:   `<a href="https://github.com/ethereum/tests/test.py">[source]</a> <p>./hive --sim eels/consume-engine --client reth</p>`,
				SummaryResult: SummaryResult{Pass: false, Details: "assertion error"},
				ClientInfo:    map[string]ClientInfo{"c1": {Name: "reth", LogFile: "client.log"}},
			},
			"2": {
				Name:          "tests/osaka/test_eip.py::test_fail_two[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: false, Details: "timeout"},
			},
			"3": {
				Name:          "tests/osaka/test_eip.py::test_pass_one[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: true},
			},
		},
	}

	besuDetail := &SuiteResult{
		ID: 2, Name: "eels/consume-engine",
		TestCases: map[string]TestCase{
			"1": {
				Name:          "tests/osaka/test_eip.py::test_fail_one[fork_Osaka-engine]-besu_default",
				SummaryResult: SummaryResult{Pass: false, Details: "bad block"},
			},
			"2": {
				Name:          "tests/osaka/test_eip.py::test_pass_one[fork_Osaka-engine]-besu_default",
				SummaryResult: SummaryResult{Pass: true},
			},
		},
	}

	rethOldDetail := &SuiteResult{
		ID: 3, Name: "eels/consume-engine",
		TestCases: map[string]TestCase{
			"1": {
				Name:          "tests/osaka/test_eip.py::test_fail_one[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: false},
			},
			"2": {
				Name:          "tests/osaka/test_eip.py::test_old_regression[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: false},
			},
			"3": {
				Name:          "tests/osaka/test_eip.py::test_fail_two[fork_Osaka-engine]-reth_default",
				SummaryResult: SummaryResult{Pass: false},
			},
		},
	}

	srv := suiteServer(t, "tg", map[string]*SuiteResult{
		"suite-reth.json":     rethDetail,
		"suite-besu.json":     besuDetail,
		"suite-reth-old.json": rethOldDetail,
	})
	s := NewStore(srv.URL, []string{"tg"})
	s.listings["tg"] = allRuns
	s.latest["tg"] = runs
	return s
}

// suiteServer serves suite files as hive's static file server does, with
// range requests.
func suiteServer(t *testing.T, group string, suites map[string]*SuiteResult) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/"+group+"/results/", func(w http.ResponseWriter, r *http.Request) {
		name := strings.TrimPrefix(r.URL.Path, "/"+group+"/results/")
		suite, ok := suites[name]
		if !ok {
			http.NotFound(w, r)
			return
		}
		data, _ := json.Marshal(suite)
		http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(data))
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func testAPI(t *testing.T) (*API, *http.ServeMux) {
	store := testFixtures(t)
	api := NewAPI(store)
	mux := http.NewServeMux()
	api.RegisterRoutes(mux)
	return api, mux
}

func getJSON(t *testing.T, mux *http.ServeMux, path string) (int, map[string]any) {
	t.Helper()
	req := httptest.NewRequest("GET", path, nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)
	var result map[string]any
	json.Unmarshal(w.Body.Bytes(), &result)
	return w.Code, result
}

// --- Index ---

func TestHandleIndex(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	endpoints, ok := body["endpoints"].([]any)
	if !ok || len(endpoints) == 0 {
		t.Error("expected non-empty endpoints list")
	}
}

// --- Groups ---

func TestHandleGroups(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	groups, ok := body["groups"].([]any)
	if !ok || len(groups) != 1 {
		t.Errorf("expected 1 group, got %v", body)
	}
}

// --- Summary ---

func TestHandleSummary(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/summary")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	if body["group"] != "tg" {
		t.Errorf("unexpected group: %v", body["group"])
	}
	clients, ok := body["clients"].(map[string]any)
	if !ok || len(clients) != 2 {
		t.Errorf("expected 2 clients, got %v", clients)
	}
	sims, ok := body["simulators"].(map[string]any)
	if !ok || len(sims) != 1 {
		t.Errorf("expected 1 simulator, got %v", sims)
	}
}

func TestHandleSummary_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/nonexistent/summary")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
	if body["error"] == nil {
		t.Error("expected error message")
	}
}

// --- Fails ---

func TestHandleFails(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/fails")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	totalFails, _ := body["total_fails"].(float64)
	if totalFails != 3 { // 2 reth + 1 besu
		t.Errorf("expected 3 total fails, got %v", totalFails)
	}
}

func TestHandleFails_ClientFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/fails?client=reth")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	clients := body["clients"].(map[string]any)
	if _, ok := clients["besu"]; ok {
		t.Error("besu should be filtered out")
	}
	if _, ok := clients["reth"]; !ok {
		t.Error("reth should be present")
	}
}

func TestHandleFails_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/nope/fails")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

// --- Passes ---

func TestHandlePasses(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/passes")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	totalPasses, _ := body["total_passes"].(float64)
	if totalPasses != 17 { // 8 reth + 9 besu
		t.Errorf("expected 17 total passes, got %v", totalPasses)
	}
}

func TestHandlePasses_SimulatorFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/passes?simulator=nonexistent")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	clients := body["clients"].(map[string]any)
	if len(clients) != 0 {
		t.Errorf("expected no clients for nonexistent simulator, got %d", len(clients))
	}
}

// --- Tests (merged endpoint) ---

func TestHandleTests_FailStatus(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 3 { // 2 reth fails + 1 besu fail
		t.Errorf("expected 3 failing tests, got %v", total)
	}
}

func TestHandleTests_PassStatus(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=pass")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 2 { // 1 reth pass + 1 besu pass
		t.Errorf("expected 2 passing tests, got %v", total)
	}
}

func TestHandleTests_AllStatus(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 5 { // all tests
		t.Errorf("expected 5 total tests, got %v", total)
	}
}

func TestHandleTests_ClientFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail&client=besu")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 1 { // only besu's fail
		t.Errorf("expected 1, got %v", total)
	}
}

func TestHandleTests_HiveURL(t *testing.T) {
	api, mux := testAPI(t)
	want := api.store.baseURL + "/#/test/tg/suite-besu?testnumber=1"

	_, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail&client=besu")
	tests := body["tests"].([]any)
	if got := tests[0].(map[string]any)["hive_url"]; got != want {
		t.Errorf("tests hive_url = %v, want %s", got, want)
	}

	_, body = getJSON(t, mux, "/api/v1/groups/tg/tests/suite-besu.json/1")
	if got := body["hive_url"]; got != want {
		t.Errorf("detail hive_url = %v, want %s", got, want)
	}
}

func TestHandleTests_RegexFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail&filter=fail_one")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 2 { // reth fail_one + besu fail_one
		t.Errorf("expected 2, got %v", total)
	}
}

func TestHandleTests_InvalidRegex(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?filter=[invalid")
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
	if body["error"] == nil {
		t.Error("expected error message")
	}
}

func TestHandleTests_FilterTooLong(t *testing.T) {
	_, mux := testAPI(t)
	longFilter := strings.Repeat("a", 1025)
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/tests?filter="+longFilter)
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
}

func TestHandleTests_Pagination(t *testing.T) {
	_, mux := testAPI(t)

	// Limit.
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail&limit=1")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	tests := body["tests"].([]any)
	total, _ := body["total"].(float64)
	if len(tests) != 1 {
		t.Errorf("expected 1 test in response, got %d", len(tests))
	}
	if total != 3 { // total should be full count
		t.Errorf("expected total=3, got %v", total)
	}

	// Offset past end.
	code, body = getJSON(t, mux, "/api/v1/groups/tg/tests?status=fail&offset=100")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	tests = body["tests"].([]any)
	if len(tests) != 0 {
		t.Errorf("expected 0 tests with large offset, got %d", len(tests))
	}
}

func TestHandleTests_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/nope/tests")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

// --- Tests Lookup ---

func TestHandleTestLookup(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests/lookup?filter=test_fail_one&client=reth")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	if body["eels_test_function"] != "test_fail_one" {
		t.Errorf("unexpected function: %v", body["eels_test_function"])
	}
	if body["pass"] != false {
		t.Error("expected pass=false")
	}
}

func TestHandleTestLookup_MissingFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests/lookup")
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
	if !strings.Contains(body["error"].(string), "filter") {
		t.Error("error should mention filter")
	}
}

func TestHandleTestLookup_NoMatch(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/tests/lookup?filter=nonexistent_test_xyz")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

func TestHandleTestLookup_InvalidRegex(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/tests/lookup?filter=[bad")
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
}

func TestHandleTestLookup_FilterTooLong(t *testing.T) {
	_, mux := testAPI(t)
	longFilter := strings.Repeat("x", 1025)
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/tests/lookup?filter="+longFilter)
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
}

// --- Test Detail (by file/testID) ---

func TestHandleTestDetail(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/tests/suite-reth.json/1")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	if body["pass"] != false {
		t.Error("expected pass=false")
	}
	if body["eels_test_function"] != "test_fail_one" {
		t.Errorf("unexpected function: %v", body["eels_test_function"])
	}
	if body["error_log"] != "assertion error" {
		t.Errorf("unexpected error_log: %v", body["error_log"])
	}
}

func TestHandleTestDetail_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/tests/suite-reth.json/999")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

func TestHandleTestDetail_InvalidPath(t *testing.T) {
	_, mux := testAPI(t)
	// Go's ServeMux cleans ".." from paths before routing (301, or 307 in newer Go).
	// Verify that our validPathSegment check catches it if a segment somehow arrives.
	// With the mux, ".." is redirected away:
	code, _ := getJSON(t, mux, "/api/v1/groups/../tests/suite-reth.json/1")
	if code != 301 && code != 307 && code != 400 {
		t.Fatalf("expected 301 or 307 (mux redirect) or 400, got %d", code)
	}
}

// --- Suites ---

func TestHandleSuites(t *testing.T) {
	_, mux := testAPI(t)
	req := httptest.NewRequest("GET", "/api/v1/groups/tg/suites", nil)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	var runs []TestRun
	json.Unmarshal(w.Body.Bytes(), &runs)
	if len(runs) != 2 {
		t.Errorf("expected 2 suite runs, got %d", len(runs))
	}
}

func TestHandleSuites_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/nope/suites")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

// --- Suite Detail ---

func TestHandleSuiteDetail(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/suites/suite-reth.json")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	cases, ok := body["tests"].([]any)
	if !ok {
		t.Fatal("expected tests list")
	}
	if len(cases) != 3 { // 2 fails + 1 pass
		t.Errorf("expected 3 test cases, got %d", len(cases))
	}
}

func TestHandleSuiteDetail_FailFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/suites/suite-reth.json?status=fail")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	cases := body["tests"].([]any)
	if len(cases) != 2 { // only fails
		t.Errorf("expected 2 failing test cases, got %d", len(cases))
	}
}

func TestHandleSuiteDetail_InvalidPath(t *testing.T) {
	_, mux := testAPI(t)
	// Go's ServeMux cleans ".." before routing.
	code, _ := getJSON(t, mux, "/api/v1/groups/tg/suites/..")
	if code != 301 && code != 307 && code != 400 {
		t.Fatalf("expected 301 or 307 (mux redirect) or 400, got %d", code)
	}
}

// --- Diff ---

func TestHandleDiff(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/diff")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	if body["group"] != "tg" {
		t.Errorf("unexpected group: %v", body["group"])
	}
	// Should have regressions, fixes, and unchanged.
	regressions, _ := body["regressions"].(map[string]any)
	fixes, _ := body["fixes"].(map[string]any)
	unchanged, _ := body["unchanged_fails"].(map[string]any)

	// test_fail_two: in both current and old -> unchanged
	// test_fail_one: in both current and old -> unchanged
	// test_old_regression: only in old -> fix
	// So we expect: unchanged has reth, fixes has reth
	if len(unchanged) == 0 {
		t.Error("expected some unchanged fails")
	}
	if len(fixes) == 0 {
		t.Error("expected some fixes (test_old_regression was in old but not current)")
	}
	// besu failures are only in current batch (no old besu runs) -> regressions
	if len(regressions) == 0 {
		t.Error("expected some regressions (besu fails only in current)")
	}
}

func TestHandleDiff_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/nope/diff")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

func TestHandleDiff_NotEnoughBatches(t *testing.T) {
	// Create a store with only one run batch.
	now := time.Now()
	s := NewStore("http://unused", []string{"g"})
	s.listings["g"] = []TestRun{{Name: "sim", Start: now, Clients: []string{"c"}}}
	s.latest["g"] = []TestRun{}
	api := NewAPI(s)
	mux := http.NewServeMux()
	api.RegisterRoutes(mux)

	code, body := getJSON(t, mux, "/api/v1/groups/g/diff")
	if code != 404 {
		t.Fatalf("expected 404, got %d; body=%v", code, body)
	}
}

// --- Search ---

func TestHandleSearch(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/search?q=fail_one")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	total, _ := body["total"].(float64)
	if total != 2 { // reth fail_one + besu fail_one
		t.Errorf("expected 2 results, got %v", total)
	}
	results := body["results"].([]any)
	if len(results) != 2 {
		t.Errorf("expected 2 results in list, got %d", len(results))
	}
}

func TestHandleSearch_StatusFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/search?q=test_&status=pass")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	results := body["results"].([]any)
	for _, r := range results {
		rm := r.(map[string]any)
		if rm["pass"] != true {
			t.Error("expected only passing results with status=pass")
		}
	}
}

func TestHandleSearch_ClientFilter(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/search?q=fail&client=besu")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	results := body["results"].([]any)
	for _, r := range results {
		rm := r.(map[string]any)
		if rm["client"] != "besu" {
			t.Errorf("expected client=besu, got %v", rm["client"])
		}
	}
}

func TestHandleSearch_Limit(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/search?q=test_&limit=1")
	if code != 200 {
		t.Fatalf("expected 200, got %d", code)
	}
	results := body["results"].([]any)
	total, _ := body["total"].(float64)
	if len(results) != 1 {
		t.Errorf("expected 1 result, got %d", len(results))
	}
	if total <= 1 {
		t.Errorf("total should be > 1 (actual matches), got %v", total)
	}
}

func TestHandleSearch_MissingQuery(t *testing.T) {
	_, mux := testAPI(t)
	code, body := getJSON(t, mux, "/api/v1/groups/tg/search")
	if code != 400 {
		t.Fatalf("expected 400, got %d", code)
	}
	if !strings.Contains(body["error"].(string), "q") {
		t.Error("error should mention missing q param")
	}
}

func TestHandleSearch_NotFound(t *testing.T) {
	_, mux := testAPI(t)
	code, _ := getJSON(t, mux, "/api/v1/groups/nope/search?q=test")
	if code != 404 {
		t.Fatalf("expected 404, got %d", code)
	}
}

// --- CORS ---

func TestCORSMiddleware(t *testing.T) {
	_, mux := testAPI(t)
	handler := corsMiddleware(mux)

	// Regular GET.
	req := httptest.NewRequest("GET", "/api/v1/groups", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Error("expected CORS origin header")
	}

	// OPTIONS preflight.
	req = httptest.NewRequest("OPTIONS", "/api/v1/groups", nil)
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != 204 {
		t.Errorf("expected 204 for OPTIONS, got %d", w.Code)
	}
}
