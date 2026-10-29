package main

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

type API struct {
	store *Store
}

func NewAPI(store *Store) *API {
	return &API{store: store}
}

func (a *API) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/v1", a.handleIndex)
	mux.HandleFunc("GET /api/v1/groups", a.handleGroups)
	mux.HandleFunc("GET /api/v1/groups/{group}/summary", a.handleSummary)
	mux.HandleFunc("GET /api/v1/groups/{group}/fails", a.handleFails)
	mux.HandleFunc("GET /api/v1/groups/{group}/passes", a.handlePasses)
	mux.HandleFunc("GET /api/v1/groups/{group}/tests", a.handleTests)
	mux.HandleFunc("GET /api/v1/groups/{group}/tests/lookup", a.handleTestLookup)
	mux.HandleFunc("GET /api/v1/groups/{group}/tests/{file}/{testID}", a.handleTestDetail)
	mux.HandleFunc("GET /api/v1/groups/{group}/suites", a.handleSuites)
	mux.HandleFunc("GET /api/v1/groups/{group}/suites/{file}", a.handleSuiteDetail)
	mux.HandleFunc("GET /api/v1/groups/{group}/diff", a.handleDiff)
	mux.HandleFunc("GET /api/v1/groups/{group}/search", a.handleSearch)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		log.Printf("json encode error: %v", err)
	}
}

// firstClient returns the normalized name and raw key of the first client in a run.
func firstClient(run TestRun) (name, key string) {
	if len(run.Clients) == 0 {
		return "", ""
	}
	key = run.Clients[0]
	name = NormalizeClientName(key)
	return
}

func validPathSegment(s string) bool {
	return s != "" && s != "." && s != ".." && !strings.Contains(s, "/")
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func (a *API) handleGroups(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, GroupsResponse{Groups: a.store.GetGroups()})
}

func (a *API) handleSummary(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	resp := GroupSummaryResponse{
		Group:      group,
		Clients:    make(map[string]*ClientSummary),
		Simulators: make(map[string]*SimSummary),
	}

	for _, run := range latest {
		sim := SimulatorName(run.Name)

		ss, ok := resp.Simulators[sim]
		if !ok {
			ss = &SimSummary{}
			resp.Simulators[sim] = ss
		}
		ss.Pass += run.Passes
		ss.Fail += run.Fails
		ss.Total += run.NTests

		for _, ck := range run.Clients {
			clientName := NormalizeClientName(ck)
			version := run.Versions[ck]
			commit := ParseCommit(clientName, version)

			cs, ok := resp.Clients[clientName]
			if !ok {
				var repo, branch string
				if suite, err := a.store.GetSuite(group, run.FileName, false); err == nil && suite.RunMetadata != nil {
					repo, branch = ExtractBuildInfo(suite.RunMetadata.ClientConfig, clientName)
				}
				cs = &ClientSummary{
					Version: CleanVersion(clientName, version),
					Repo:    repo,
					Branch:  branch,
					Commit:  commit,
				}
				resp.Clients[clientName] = cs
			}
			cs.Pass += run.Passes
			cs.Fail += run.Fails
			if run.Timeout {
				cs.Timeout += run.NTests
			}
		}

		if resp.LastRun.IsZero() || run.Start.After(resp.LastRun) {
			resp.LastRun = run.Start
		}
	}

	writeJSON(w, 200, resp)
}

func (a *API) handleFails(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	filterClient := r.URL.Query().Get("client")
	filterSim := r.URL.Query().Get("simulator")

	resp := FailsResponse{
		Group:   group,
		Clients: make(map[string]*ClientFails),
	}

	for _, run := range latest {
		if run.Fails == 0 {
			continue
		}
		sim := SimulatorName(run.Name)
		if filterSim != "" && sim != filterSim {
			continue
		}
		clientName, clientKey := firstClient(run)
		if filterClient != "" && clientName != filterClient {
			continue
		}

		suite, err := a.store.GetSuite(group, run.FileName, false)
		if err != nil {
			continue
		}

		version := run.Versions[clientKey]
		cf, ok := resp.Clients[clientName]
		if !ok {
			var repo, branch string
			if suite.RunMetadata != nil {
				repo, branch = ExtractBuildInfo(suite.RunMetadata.ClientConfig, clientName)
			}
			cf = &ClientFails{
				Version:    CleanVersion(clientName, version),
				Repo:       repo,
				Branch:     branch,
				Commit:     ParseCommit(clientName, version),
				Simulators: make(map[string]*SimulatorFails),
			}
			resp.Clients[clientName] = cf
		}

		if _, ok := cf.Simulators[sim]; !ok {
			var fixturesRelease string
			if suite.RunMetadata != nil {
				fixturesRelease = ExtractFixturesRelease(suite.RunMetadata)
			}
			cf.Simulators[sim] = &SimulatorFails{
				RunDate:         run.Start.Format(time.RFC3339),
				SuiteFile:       run.FileName,
				FixturesRelease: fixturesRelease,
				TotalTests:      run.NTests,
				TotalFails:      run.Fails,
			}
			cf.TotalFails += run.Fails
			resp.TotalFails += run.Fails
		}
	}

	// Compute rates.
	var totalTests int
	for _, cf := range resp.Clients {
		var clientTotal int
		for _, sf := range cf.Simulators {
			if sf.TotalTests > 0 {
				sf.FailRate = float64(sf.TotalFails) / float64(sf.TotalTests) * 100
			}
			clientTotal += sf.TotalTests
		}
		if clientTotal > 0 {
			cf.FailRate = float64(cf.TotalFails) / float64(clientTotal) * 100
		}
		totalTests += clientTotal
	}
	if totalTests > 0 {
		resp.FailRate = float64(resp.TotalFails) / float64(totalTests) * 100
	}

	writeJSON(w, 200, resp)
}

func (a *API) handleTests(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	filterClient := r.URL.Query().Get("client")
	filterSim := r.URL.Query().Get("simulator")
	filterPattern := r.URL.Query().Get("filter")
	statusFilter := r.URL.Query().Get("status") // "fail", "pass", or "" for all
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))

	var filterRe *regexp.Regexp
	if filterPattern != "" {
		if len(filterPattern) > 1024 {
			writeError(w, 400, "filter pattern too long")
			return
		}
		var err error
		filterRe, err = regexp.Compile("(?i)" + filterPattern)
		if err != nil {
			writeError(w, 400, "invalid filter regex: "+err.Error())
			return
		}
	}

	var tests []TestListEntry
	for _, run := range latest {
		if statusFilter == "fail" && run.Fails == 0 {
			continue
		}
		if statusFilter == "pass" && run.Passes == 0 {
			continue
		}
		sim := SimulatorName(run.Name)
		if filterSim != "" && sim != filterSim {
			continue
		}
		clientName, _ := firstClient(run)
		if filterClient != "" && clientName != filterClient {
			continue
		}

		suite, err := a.store.GetSuite(group, run.FileName, true)
		if err != nil {
			continue
		}

		for _, t := range suite.Tests {
			if statusFilter == "fail" && t.Pass {
				continue
			}
			if statusFilter == "pass" && !t.Pass {
				continue
			}
			if filterRe != nil && !filterRe.MatchString(t.Name) {
				continue
			}
			tests = append(tests, a.testEntry(group, run, clientName, sim, t))
		}
	}

	total := len(tests)
	if offset > 0 {
		if offset >= len(tests) {
			tests = tests[:0]
		} else {
			tests = tests[offset:]
		}
	}
	if limit > 0 && limit < len(tests) {
		tests = tests[:limit]
	}

	writeJSON(w, 200, TestListResponse{Group: group, Total: total, Tests: tests})
}

func (a *API) handlePasses(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	filterClient := r.URL.Query().Get("client")
	filterSim := r.URL.Query().Get("simulator")

	resp := PassesResponse{
		Group:   group,
		Clients: make(map[string]*ClientPasses),
	}

	for _, run := range latest {
		if run.Passes == 0 {
			continue
		}
		sim := SimulatorName(run.Name)
		if filterSim != "" && sim != filterSim {
			continue
		}
		clientName, clientKey := firstClient(run)
		if filterClient != "" && clientName != filterClient {
			continue
		}

		suite, err := a.store.GetSuite(group, run.FileName, false)
		if err != nil {
			continue
		}

		version := run.Versions[clientKey]
		cp, ok := resp.Clients[clientName]
		if !ok {
			var repo, branch string
			if suite.RunMetadata != nil {
				repo, branch = ExtractBuildInfo(suite.RunMetadata.ClientConfig, clientName)
			}
			cp = &ClientPasses{
				Version:    CleanVersion(clientName, version),
				Repo:       repo,
				Branch:     branch,
				Commit:     ParseCommit(clientName, version),
				Simulators: make(map[string]*SimulatorPasses),
			}
			resp.Clients[clientName] = cp
		}

		if _, ok := cp.Simulators[sim]; !ok {
			var fixturesRelease string
			if suite.RunMetadata != nil {
				fixturesRelease = ExtractFixturesRelease(suite.RunMetadata)
			}
			cp.Simulators[sim] = &SimulatorPasses{
				RunDate:         run.Start.Format(time.RFC3339),
				SuiteFile:       run.FileName,
				FixturesRelease: fixturesRelease,
				TotalTests:      run.NTests,
				TotalPasses:     run.Passes,
			}
			cp.TotalPasses += run.Passes
			resp.TotalPasses += run.Passes
		}
	}

	// Compute rates.
	var totalTests int
	for _, cp := range resp.Clients {
		var clientTotal int
		for _, sp := range cp.Simulators {
			if sp.TotalTests > 0 {
				sp.PassRate = float64(sp.TotalPasses) / float64(sp.TotalTests) * 100
			}
			clientTotal += sp.TotalTests
		}
		if clientTotal > 0 {
			cp.PassRate = float64(cp.TotalPasses) / float64(clientTotal) * 100
		}
		totalTests += clientTotal
	}
	if totalTests > 0 {
		resp.PassRate = float64(resp.TotalPasses) / float64(totalTests) * 100
	}

	writeJSON(w, 200, resp)
}

// testEntry describes one test of a run, for lists, search and diff.
func (a *API) testEntry(group string, run TestRun, client, sim string, t SuiteTest) TestListEntry {
	mod, fn := SplitTestName(t.Name)
	id := strconv.FormatUint(uint64(t.ID), 10)
	return TestListEntry{
		Client:           client,
		Simulator:        sim,
		EELSTestModule:   mod,
		EELSTestFunction: fn,
		PytestID:         t.Name,
		Fork:             ExtractFork(t.Name),
		Pass:             t.Pass,
		SuiteTestIndex:   id,
		SuiteFile:        run.FileName,
		TestURL:          t.Source,
		HiveURL:          a.store.TestURL(group, run.FileName, id),
	}
}

// testDetail reads one test case and builds its detail. A test that does not
// exist gives errTestNotFound.
func (a *API) testDetail(group, file string, id uint32, fullLog bool) (TestDetailResponse, error) {
	tc, suite, err := a.store.GetTestCase(group, file, id)
	if err != nil {
		return TestDetailResponse{}, err
	}
	return a.buildTestDetail(group, file, suite, strconv.FormatUint(uint64(id), 10), *tc, fullLog), nil
}

// buildTestDetail constructs a TestDetailResponse from a test case, fetching logs as needed.
func (a *API) buildTestDetail(group, file string, suite *Suite, testID string, tc TestCase, fullLog bool) TestDetailResponse {
	mod, fn := SplitTestName(tc.Name)
	pytestID := ExtractPytestID(tc.Name)

	resp := TestDetailResponse{
		EELSTestModule:   mod,
		EELSTestFunction: fn,
		PytestID:         pytestID,
		Fork:             ExtractFork(tc.Name),
		SuiteTestIndex:   testID,
		TestURL:          ExtractSourceURL(tc.Description),
		HiveURL:          a.store.TestURL(group, file, testID),
		Pass:             tc.SummaryResult.Pass,
		FillCommand:      BuildFillCommand(pytestID),
		HiveCommand:      ExtractHiveCommand(tc.Description),
		ConsumeCommand:   ExtractConsumeCommand(tc.Description),
		ErrorLog:         tc.SummaryResult.Details,
	}

	var clientLogFile string
	for _, ci := range tc.ClientInfo {
		resp.Client = ci.Name
		clientLogFile = ci.LogFile
		break
	}

	if tc.SummaryResult.Log != nil && suite.TestDetailsLog != "" {
		logData, err := a.store.FetchLog(
			group, suite.TestDetailsLog,
			tc.SummaryResult.Log.Begin, tc.SummaryResult.Log.End,
		)
		if err == nil {
			resp.DetailLog = logData
		}
	}

	if clientLogFile != "" {
		logData, err := a.store.FetchLog(group, clientLogFile, -1, -1)
		if err == nil {
			if fullLog {
				resp.ClientLog = logData
			} else {
				resp.ClientLog = tailLines(logData, 30)
			}
		}
	}

	return resp
}

// handleTestLookup looks up a test by regex filter + optional client, returns full detail.
func (a *API) handleTestLookup(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	filterPattern := r.URL.Query().Get("filter")
	if filterPattern == "" {
		writeError(w, 400, "missing required parameter: filter")
		return
	}
	if len(filterPattern) > 1024 {
		writeError(w, 400, "filter pattern too long")
		return
	}
	filterRe, err := regexp.Compile("(?i)" + filterPattern)
	if err != nil {
		writeError(w, 400, "invalid filter regex: "+err.Error())
		return
	}

	filterClient := r.URL.Query().Get("client")
	filterSim := r.URL.Query().Get("simulator")
	fullLog := r.URL.Query().Get("full_log") == "true"

	for _, run := range latest {
		sim := SimulatorName(run.Name)
		if filterSim != "" && sim != filterSim {
			continue
		}
		clientName, _ := firstClient(run)
		if filterClient != "" && clientName != filterClient {
			continue
		}

		suite, err := a.store.GetSuite(group, run.FileName, true)
		if err != nil {
			continue
		}

		for _, t := range suite.Tests {
			if !filterRe.MatchString(t.Name) {
				continue
			}
			resp, err := a.testDetail(group, run.FileName, t.ID, fullLog)
			if err != nil {
				writeError(w, 502, "failed to fetch test detail: "+err.Error())
				return
			}
			writeJSON(w, 200, resp)
			return
		}
	}

	writeError(w, 404, "no test matching filter")
}

// handleTestDetail returns full detail for a single test: commands, logs, etc.
func (a *API) handleTestDetail(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	file := r.PathValue("file")
	testID := r.PathValue("testID")

	if !validPathSegment(group) || !validPathSegment(file) {
		writeError(w, 400, "invalid path parameter")
		return
	}

	id, err := strconv.ParseUint(testID, 10, 32)
	if err != nil {
		writeError(w, 404, "test case not found")
		return
	}

	fullLog := r.URL.Query().Get("full_log") == "true"
	resp, err := a.testDetail(group, file, uint32(id), fullLog)
	if errors.Is(err, errTestNotFound) {
		writeError(w, 404, "test case not found")
		return
	}
	if err != nil {
		writeError(w, 502, "failed to fetch test detail: "+err.Error())
		return
	}
	writeJSON(w, 200, resp)
}

func (a *API) handleSuites(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	runs, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}
	writeJSON(w, 200, runs)
}

func (a *API) handleSuiteDetail(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	file := r.PathValue("file")

	if !validPathSegment(group) || !validPathSegment(file) {
		writeError(w, 400, "invalid path parameter")
		return
	}

	suite, err := a.store.GetSuite(group, file, true)
	if err != nil {
		writeError(w, 502, "failed to fetch suite detail: "+err.Error())
		return
	}

	statusFilter := r.URL.Query().Get("status")
	if statusFilter == "fail" {
		filtered := make([]SuiteTest, 0)
		for _, t := range suite.Tests {
			if !t.Pass {
				filtered = append(filtered, t)
			}
		}
		result := *suite
		result.Tests = filtered
		writeJSON(w, 200, result)
		return
	}

	writeJSON(w, 200, suite)
}

func (a *API) handleDiff(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	allRuns, ok := a.store.GetAllRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	type runBatch struct {
		time time.Time
		runs []TestRun
	}

	var batches []runBatch
	for _, run := range allRuns {
		placed := false
		for i := range batches {
			diff := run.Start.Sub(batches[i].time)
			if diff < 0 {
				diff = -diff
			}
			if diff < 4*time.Hour {
				batches[i].runs = append(batches[i].runs, run)
				placed = true
				break
			}
		}
		if !placed {
			batches = append(batches, runBatch{time: run.Start, runs: []TestRun{run}})
		}
	}

	if len(batches) < 2 {
		writeError(w, 404, "need at least 2 run batches for diff")
		return
	}

	currentBatch := batches[0]
	previousBatch := batches[1]

	currentFails := a.collectBatchFails(group, currentBatch.runs)
	previousFails := a.collectBatchFails(group, previousBatch.runs)

	// With ?counts=true only the totals come back, not every test, which
	// can be tens of MB for a group with many failures.
	countsOnly := r.URL.Query().Get("counts") == "true"

	resp := DiffResponse{
		Group:          group,
		FromRun:        previousBatch.time,
		ToRun:          currentBatch.time,
		Regressions:    make(map[string]*ClientDiffFails),
		Fixes:          make(map[string]*ClientDiffFails),
		UnchangedFails: make(map[string]*ClientDiffFails),
	}

	addDiffEntry := func(target map[string]*ClientDiffFails, client, sim string, entry diffEntry) {
		cf, ok := target[client]
		if !ok {
			cf = &ClientDiffFails{Simulators: make(map[string]*SimDiffFails)}
			target[client] = cf
		}
		sf, ok := cf.Simulators[sim]
		if !ok {
			sf = &SimDiffFails{}
			cf.Simulators[sim] = sf
		}
		if !countsOnly {
			sf.Tests = append(sf.Tests, a.testEntry(group, entry.run, client, sim, entry.test))
		}
		sf.TotalFails++
		cf.TotalFails++
	}

	for key, entry := range currentFails {
		if _, wasFailing := previousFails[key]; wasFailing {
			addDiffEntry(resp.UnchangedFails, entry.client, entry.sim, entry)
		} else {
			addDiffEntry(resp.Regressions, entry.client, entry.sim, entry)
		}
	}
	for key, entry := range previousFails {
		if _, stillFailing := currentFails[key]; !stillFailing {
			addDiffEntry(resp.Fixes, entry.client, entry.sim, entry)
		}
	}

	writeJSON(w, 200, resp)
}

func (a *API) handleIndex(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"endpoints": []map[string]string{
			{"method": "GET", "path": "/api/v1/groups", "description": "List all available test result groups"},
			{"method": "GET", "path": "/api/v1/groups/{group}/summary", "description": "Summary with pass/fail counts per client and simulator"},
			{"method": "GET", "path": "/api/v1/groups/{group}/fails", "description": "Fail summary with rates per client/simulator. Filters: ?client=, ?simulator="},
			{"method": "GET", "path": "/api/v1/groups/{group}/passes", "description": "Pass summary with rates per client/simulator. Filters: ?client=, ?simulator="},
			{"method": "GET", "path": "/api/v1/groups/{group}/tests", "description": "List tests. Filters: ?status=fail|pass, ?client=, ?simulator=, ?filter=, ?limit=, ?offset="},
			{"method": "GET", "path": "/api/v1/groups/{group}/tests/lookup", "description": "Find first test matching filter. Params: ?filter= (required), ?client=, ?simulator=, ?full_log=true"},
			{"method": "GET", "path": "/api/v1/groups/{group}/tests/{file}/{testID}", "description": "Full detail for a single test: commands, logs, error details. Use ?full_log=true for complete client log"},
			{"method": "GET", "path": "/api/v1/groups/{group}/suites", "description": "List latest suite runs"},
			{"method": "GET", "path": "/api/v1/groups/{group}/suites/{file}", "description": "Suite run metadata and tests. Filter: ?status=fail"},
			{"method": "GET", "path": "/api/v1/groups/{group}/diff", "description": "Diff between two most recent run batches: regressions, fixes, unchanged. ?counts=true for totals only"},
			{"method": "GET", "path": "/api/v1/groups/{group}/search", "description": "Search tests by name. Params: ?q= (required), ?client=, ?simulator=, ?status=fail|pass, ?limit="},
		},
	})
}

func (a *API) handleSearch(w http.ResponseWriter, r *http.Request) {
	group := r.PathValue("group")
	latest, ok := a.store.GetLatestRuns(group)
	if !ok {
		writeError(w, 404, "group not found")
		return
	}

	query := r.URL.Query().Get("q")
	if query == "" {
		writeError(w, 400, "missing required parameter: q")
		return
	}

	filterClient := r.URL.Query().Get("client")
	filterSim := r.URL.Query().Get("simulator")
	filterStatus := r.URL.Query().Get("status")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = 50
	}

	queryLower := strings.ToLower(query)
	var results []TestListEntry
	total := 0

	for _, run := range latest {
		sim := SimulatorName(run.Name)
		if filterSim != "" && sim != filterSim {
			continue
		}
		clientName, _ := firstClient(run)
		if filterClient != "" && clientName != filterClient {
			continue
		}

		suite, err := a.store.GetSuite(group, run.FileName, true)
		if err != nil {
			continue
		}

		for _, t := range suite.Tests {
			if !strings.Contains(strings.ToLower(t.Name), queryLower) {
				continue
			}
			if filterStatus == "fail" && t.Pass {
				continue
			}
			if filterStatus == "pass" && !t.Pass {
				continue
			}

			total++
			if len(results) >= limit {
				continue
			}

			results = append(results, a.testEntry(group, run, clientName, sim, t))
		}
	}

	writeJSON(w, 200, SearchResponse{
		Group:   group,
		Query:   query,
		Total:   total,
		Results: results,
	})
}

type diffEntry struct {
	run    TestRun
	client string
	sim    string
	test   SuiteTest
}

func (a *API) collectBatchFails(group string, runs []TestRun) map[string]diffEntry {
	// Read the batch's suites in parallel first, since an older batch is
	// usually not cached yet.
	var wg sync.WaitGroup
	for _, run := range runs {
		if run.Fails == 0 {
			continue
		}
		wg.Add(1)
		go func(fileName string) {
			defer wg.Done()
			a.store.GetSuite(group, fileName, true)
		}(run.FileName)
	}
	wg.Wait()

	fails := make(map[string]diffEntry)
	for _, run := range runs {
		if run.Fails == 0 {
			continue
		}
		suite, err := a.store.GetSuite(group, run.FileName, true)
		if err != nil {
			continue
		}
		clientName, _ := firstClient(run)
		sim := SimulatorName(run.Name)
		for _, t := range suite.Tests {
			if t.Pass {
				continue
			}
			key := clientName + "/" + run.Name + "/" + t.Name
			fails[key] = diffEntry{run: run, client: clientName, sim: sim, test: t}
		}
	}
	return fails
}
