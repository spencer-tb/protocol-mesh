package main

import "time"

// --- Hive data types (from listing.jsonl and result JSON files) ---

// TestRun represents one line from listing.jsonl — a single suite run for one client.
type TestRun struct {
	Name     string            `json:"name"`
	NTests   int               `json:"ntests"`
	Passes   int               `json:"passes"`
	Fails    int               `json:"fails"`
	Timeout  bool              `json:"timeout"`
	Clients  []string          `json:"clients"`
	Versions map[string]string `json:"versions"`
	Start    time.Time         `json:"start"`
	FileName string            `json:"fileName"`
	Size     int64             `json:"size"`
	SimLog   string            `json:"simLog"`
}

// SuiteResult represents the full detail JSON for a test suite run.
type SuiteResult struct {
	ID             int                 `json:"id"`
	Name           string              `json:"name"`
	Description    string              `json:"description"`
	ClientVersions map[string]string   `json:"clientVersions"`
	TestCases      map[string]TestCase `json:"testCases"`
	SimLog         string              `json:"simLog"`
	TestDetailsLog string              `json:"testDetailsLog"`
	RunMetadata    *RunMetadata        `json:"runMetadata,omitempty"`
}

type TestCase struct {
	Name          string                `json:"name"`
	Description   string                `json:"description"`
	Start         time.Time             `json:"start"`
	End           time.Time             `json:"end"`
	SummaryResult SummaryResult         `json:"summaryResult"`
	ClientInfo    map[string]ClientInfo `json:"clientInfo"`
}

// Suite is what the store keeps of a suite file: its run metadata and, per
// test, only what listing, filtering, diffing and linking need. A test's
// description, error and log locations are read again, from its own bytes of
// the file, when its detail is asked for.
type Suite struct {
	RunMetadata    *RunMetadata `json:"runMetadata,omitempty"`
	TestDetailsLog string       `json:"testDetailsLog,omitempty"`
	Tests          []SuiteTest  `json:"tests"` // sorted by ID
	// HasTests is false when only the run metadata at the top of the file
	// was read.
	HasTests bool `json:"-"`
}

// SuiteTest is one test case of a suite. Name and Source are interned, so a
// test that several clients ran is held once.
type SuiteTest struct {
	Name   string `json:"name"`             // pytest ID, without the client suffix
	Source string `json:"source,omitempty"` // the test's [source] link
	Offset int64  `json:"-"`                // where the test case is in the suite file
	Size   int32  `json:"-"`
	ID     uint32 `json:"id"`
	Pass   bool   `json:"pass"`
}

type SummaryResult struct {
	Pass    bool      `json:"pass"`
	Timeout bool      `json:"timeout"`
	Details string    `json:"details,omitempty"`
	Log     *LogRange `json:"log,omitempty"`
}

type LogRange struct {
	Begin int64 `json:"begin"`
	End   int64 `json:"end"`
}

type ClientInfo struct {
	ID             string `json:"id"`
	IP             string `json:"ip"`
	Name           string `json:"name"`
	InstantiatedAt string `json:"instantiatedAt"`
	LogFile        string `json:"logFile"`
}

type RunMetadata struct {
	HiveCommand  []string      `json:"hiveCommand"`
	HiveVersion  *HiveVersion  `json:"hiveVersion,omitempty"`
	ClientConfig *ClientConfig `json:"clientConfig,omitempty"`
}

type HiveVersion struct {
	Commit     string `json:"commit"`
	CommitDate string `json:"commitDate"`
	Branch     string `json:"branch"`
	Dirty      bool   `json:"dirty"`
}

type ClientConfig struct {
	FilePath string               `json:"filePath"`
	Content  *ClientConfigContent `json:"content,omitempty"`
}

type ClientConfigContent struct {
	Clients []ClientConfigEntry `json:"clients"`
}

type ClientConfigEntry struct {
	Client     string            `json:"client"`
	Nametag    string            `json:"nametag"`
	Dockerfile string            `json:"dockerfile"`
	BuildArgs  map[string]string `json:"build_args"`
}

// --- API response types ---

type GroupsResponse struct {
	Groups []string `json:"groups"`
}

type GroupSummaryResponse struct {
	Group      string                    `json:"group"`
	LastRun    time.Time                 `json:"last_run"`
	Clients    map[string]*ClientSummary `json:"clients"`
	Simulators map[string]*SimSummary    `json:"simulators"`
}

type ClientSummary struct {
	Version string `json:"version"`
	Repo    string `json:"repo,omitempty"`
	Branch  string `json:"branch,omitempty"`
	Commit  string `json:"commit,omitempty"`
	Pass    int    `json:"pass"`
	Fail    int    `json:"fail"`
	Timeout int    `json:"timeout"`
}

type SimSummary struct {
	Pass  int `json:"pass"`
	Fail  int `json:"fail"`
	Total int `json:"total"`
}

type FailsResponse struct {
	Group      string                  `json:"group"`
	TotalFails int                     `json:"total_fails"`
	FailRate   float64                 `json:"fail_rate"`
	Clients    map[string]*ClientFails `json:"clients"`
}

type ClientFails struct {
	Version    string                     `json:"version"`
	Repo       string                     `json:"repo,omitempty"`
	Branch     string                     `json:"branch,omitempty"`
	Commit     string                     `json:"commit,omitempty"`
	TotalFails int                        `json:"total_fails"`
	FailRate   float64                    `json:"fail_rate"`
	Simulators map[string]*SimulatorFails `json:"simulators"`
}

type SimulatorFails struct {
	RunDate         string  `json:"run_date"`
	SuiteFile       string  `json:"suite_file"`
	FixturesRelease string  `json:"fixtures_release"`
	TotalFails      int     `json:"total_fails"`
	FailRate        float64 `json:"fail_rate"`
	TotalTests      int     `json:"total_tests"`
}

type PassesResponse struct {
	Group       string                   `json:"group"`
	TotalPasses int                      `json:"total_passes"`
	PassRate    float64                  `json:"pass_rate"`
	Clients     map[string]*ClientPasses `json:"clients"`
}

type ClientPasses struct {
	Version     string                      `json:"version"`
	Repo        string                      `json:"repo,omitempty"`
	Branch      string                      `json:"branch,omitempty"`
	Commit      string                      `json:"commit,omitempty"`
	TotalPasses int                         `json:"total_passes"`
	PassRate    float64                     `json:"pass_rate"`
	Simulators  map[string]*SimulatorPasses `json:"simulators"`
}

type SimulatorPasses struct {
	RunDate         string  `json:"run_date"`
	SuiteFile       string  `json:"suite_file"`
	FixturesRelease string  `json:"fixtures_release"`
	TotalPasses     int     `json:"total_passes"`
	PassRate        float64 `json:"pass_rate"`
	TotalTests      int     `json:"total_tests"`
}

// TestListResponse is returned by /tests.
type TestListResponse struct {
	Group string          `json:"group"`
	Total int             `json:"total"`
	Tests []TestListEntry `json:"tests"`
}

type TestListEntry struct {
	Client           string `json:"client"`
	Simulator        string `json:"simulator"`
	EELSTestModule   string `json:"eels_test_module"`
	EELSTestFunction string `json:"eels_test_function"`
	PytestID         string `json:"pytest_id"`
	Fork             string `json:"fork"`
	Pass             bool   `json:"pass"`
	SuiteTestIndex   string `json:"hive_suite_test_index"`
	SuiteFile        string `json:"suite_file"`
	TestURL          string `json:"eels_test_url"`
	HiveURL          string `json:"hive_url"`
}

// TestDetailResponse is the full detail for a single test.
type TestDetailResponse struct {
	EELSTestModule   string `json:"eels_test_module"`
	EELSTestFunction string `json:"eels_test_function"`
	PytestID         string `json:"pytest_id"`
	Fork             string `json:"fork"`
	SuiteTestIndex   string `json:"hive_suite_test_index"`
	TestURL          string `json:"eels_test_url"`
	HiveURL          string `json:"hive_url"`
	Client           string `json:"client"`
	Pass             bool   `json:"pass"`
	FillCommand      string `json:"fill_command"`
	HiveCommand      string `json:"hive_command"`
	ConsumeCommand   string `json:"consume_command"`
	ErrorLog         string `json:"error_log,omitempty"`
	DetailLog        string `json:"detail_log,omitempty"`
	ClientLog        string `json:"client_log,omitempty"`
}

type DiffResponse struct {
	Group          string                      `json:"group"`
	FromRun        time.Time                   `json:"from_run"`
	ToRun          time.Time                   `json:"to_run"`
	Regressions    map[string]*ClientDiffFails `json:"regressions"`
	Fixes          map[string]*ClientDiffFails `json:"fixes"`
	UnchangedFails map[string]*ClientDiffFails `json:"unchanged_fails"`
}

type ClientDiffFails struct {
	TotalFails int                      `json:"total_fails"`
	Simulators map[string]*SimDiffFails `json:"simulators"`
}

type SimDiffFails struct {
	TotalFails int             `json:"total_fails"`
	Tests      []TestListEntry `json:"tests,omitempty"`
}

type SearchResponse struct {
	Group   string          `json:"group"`
	Query   string          `json:"query"`
	Total   int             `json:"total"`
	Results []TestListEntry `json:"results"`
}
