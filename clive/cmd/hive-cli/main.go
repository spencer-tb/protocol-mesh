package main

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"text/tabwriter"
	"time"
)

var apiURL = "http://localhost:8080"

func main() {
	if u := os.Getenv("HIVE_API_URL"); u != "" {
		apiURL = strings.TrimRight(u, "/")
	}

	args := os.Args[1:]
	if len(args) == 0 {
		printUsage()
		os.Exit(1)
	}

	// Check for --api flag anywhere.
	for i, a := range args {
		if strings.HasPrefix(a, "--api=") {
			apiURL = strings.TrimRight(strings.TrimPrefix(a, "--api="), "/")
			args = append(args[:i], args[i+1:]...)
			break
		}
	}

	// Check for --json flag.
	jsonOutput := false
	for i, a := range args {
		if a == "--json" {
			jsonOutput = true
			args = append(args[:i], args[i+1:]...)
			break
		}
	}

	cmd := args[0]
	args = args[1:]

	var err error
	switch cmd {
	case "groups":
		err = cmdGroups(jsonOutput)
	case "summary":
		err = requireGroup(args, jsonOutput, cmdSummary)
	case "fails":
		err = requireGroup(args, jsonOutput, cmdFails)
	case "passes":
		err = requireGroup(args, jsonOutput, cmdPasses)
	case "tests":
		err = requireGroup(args, jsonOutput, cmdTests)
	case "detail":
		err = cmdDetail(args, jsonOutput)
	case "diff":
		err = requireGroup(args, jsonOutput, cmdDiff)
	case "search":
		err = cmdSearch(args, jsonOutput)
	case "help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		printUsage()
		os.Exit(1)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `clive — CLI for Ethereum Hive test results

Usage: clive <command> [options]

Commands:
  groups                      List available test result groups
  summary  <group>            Client/simulator overview with pass/fail counts
  fails    <group> [flags]    Fail summary with rates per client
  passes   <group> [flags]    Pass summary with rates per client
  tests    <group> [flags]    List individual test cases
  detail   <group> [flags]    Full detail for a test (commands, logs, errors)
  diff     <group>            Compare latest two runs (regressions/fixes)
  search   <group> <query>    Search tests by name
  help                        Show this help

Common flags:
  --client=X       Filter by client name
  --simulator=Y    Filter by simulator
  --filter=REGEX   Filter tests by name (regex, case-insensitive)
  --json           Output raw JSON instead of text
  --api=URL        API base URL (default: $HIVE_API_URL or http://localhost:8080)

Tests flags:
  --status=fail|pass  Filter by status (default: fail)
  --limit=N           Max results (default: 50)

Examples:
  clive groups
  clive summary bal-quick
  clive fails bal-quick --client=besu
  clive tests bal-quick --client=besu --filter="eip7702"
  clive detail bal-quick --client=besu --filter="test_bal_invalid_account"
  clive diff bal-quick
`)
}

// --- helpers ---

func requireGroup(args []string, jsonOut bool, fn func(string, []string, bool) error) error {
	if len(args) == 0 {
		return fmt.Errorf("group argument required")
	}
	return fn(args[0], args[1:], jsonOut)
}

func parseFlags(args []string) map[string]string {
	flags := make(map[string]string)
	for _, a := range args {
		if strings.HasPrefix(a, "--") {
			a = strings.TrimPrefix(a, "--")
			if idx := strings.IndexByte(a, '='); idx >= 0 {
				flags[a[:idx]] = a[idx+1:]
			} else {
				flags[a] = "true"
			}
		}
	}
	return flags
}

func apiGet(path string, params url.Values) ([]byte, error) {
	u := apiURL + path
	if len(params) > 0 {
		u += "?" + params.Encode()
	}
	resp, err := http.Get(u)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return body, nil
}

func printJSON(data []byte) {
	var v any
	if json.Unmarshal(data, &v) == nil {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		enc.Encode(v)
	} else {
		os.Stdout.Write(data)
	}
}

func roundRate(r float64) string {
	return fmt.Sprintf("%.1f%%", math.Round(r*10)/10)
}

// --- commands ---

func cmdGroups(jsonOut bool) error {
	data, err := apiGet("/api/v1/groups", nil)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}
	var resp struct {
		Groups []string `json:"groups"`
	}
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}
	fmt.Println("Available groups:")
	for _, g := range resp.Groups {
		fmt.Printf("  %s\n", g)
	}
	return nil
}

func cmdSummary(group string, args []string, jsonOut bool) error {
	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/summary", group), nil)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}
	var resp struct {
		Group   string    `json:"group"`
		LastRun time.Time `json:"last_run"`
		Clients map[string]struct {
			Version string `json:"version"`
			Repo    string `json:"repo"`
			Branch  string `json:"branch"`
			Pass    int    `json:"pass"`
			Fail    int    `json:"fail"`
			Timeout int    `json:"timeout"`
		} `json:"clients"`
		Simulators map[string]struct {
			Pass  int `json:"pass"`
			Fail  int `json:"fail"`
			Total int `json:"total"`
		} `json:"simulators"`
	}
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	fmt.Printf("%s SUMMARY\n", strings.ToUpper(group))
	fmt.Printf("Last run: %s\n\n", resp.LastRun.Format(time.RFC3339))

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "CLIENT\tVERSION\tPASS\tFAIL\tTIMEOUT\tRATE\tREPO\tBRANCH")
	fmt.Fprintln(w, "------\t-------\t----\t----\t-------\t----\t----\t------")

	names := sortedKeys(resp.Clients)
	for _, name := range names {
		c := resp.Clients[name]
		total := c.Pass + c.Fail
		rate := ""
		if total > 0 {
			rate = roundRate(float64(c.Pass) / float64(total) * 100)
		}
		fmt.Fprintf(w, "%s\t%s\t%d\t%d\t%d\t%s\t%s\t%s\n",
			name, c.Version, c.Pass, c.Fail, c.Timeout, rate, c.Repo, c.Branch)
	}
	w.Flush()

	fmt.Println()
	w = tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "SIMULATOR\tPASS\tFAIL\tTOTAL")
	fmt.Fprintln(w, "---------\t----\t----\t-----")
	for sim, s := range resp.Simulators {
		fmt.Fprintf(w, "%s\t%d\t%d\t%d\n", sim, s.Pass, s.Fail, s.Total)
	}
	w.Flush()

	return nil
}

type failsResp struct {
	Group      string  `json:"group"`
	TotalFails int     `json:"total_fails"`
	FailRate   float64 `json:"fail_rate"`
	Clients    map[string]struct {
		Version    string  `json:"version"`
		Repo       string  `json:"repo"`
		Branch     string  `json:"branch"`
		Commit     string  `json:"commit"`
		TotalFails int     `json:"total_fails"`
		FailRate   float64 `json:"fail_rate"`
		Simulators map[string]struct {
			TotalFails int     `json:"total_fails"`
			FailRate   float64 `json:"fail_rate"`
			TotalTests int     `json:"total_tests"`
		} `json:"simulators"`
	} `json:"clients"`
}

func cmdFails(group string, args []string, jsonOut bool) error {
	flags := parseFlags(args)
	params := url.Values{}
	if v := flags["client"]; v != "" {
		params.Set("client", v)
	}
	if v := flags["simulator"]; v != "" {
		params.Set("simulator", v)
	}

	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/fails", group), params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}

	var resp failsResp
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	fmt.Printf("%s FAILS — %d total (%s fail rate)\n\n", strings.ToUpper(group), resp.TotalFails, roundRate(resp.FailRate))

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "CLIENT\tVERSION\tFAILS\tTOTAL\tFAIL RATE\tREPO\tBRANCH")
	fmt.Fprintln(w, "------\t-------\t-----\t-----\t---------\t----\t------")

	type clientEntry struct {
		name       string
		version    string
		repo       string
		branch     string
		totalFails int
		failRate   float64
		totalTests int
	}
	var rows []clientEntry
	for name, c := range resp.Clients {
		var total int
		for _, s := range c.Simulators {
			total += s.TotalTests
		}
		rows = append(rows, clientEntry{
			name: name, version: c.Version, repo: c.Repo, branch: c.Branch,
			totalFails: c.TotalFails, failRate: c.FailRate, totalTests: total,
		})
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].failRate < rows[j].failRate
	})

	for _, r := range rows {
		fmt.Fprintf(w, "%s\t%s\t%d\t%d\t%s\t%s\t%s\n",
			r.name, r.version, r.totalFails, r.totalTests, roundRate(r.failRate), r.repo, r.branch)
	}
	w.Flush()
	return nil
}

type passesResp struct {
	Group       string  `json:"group"`
	TotalPasses int     `json:"total_passes"`
	PassRate    float64 `json:"pass_rate"`
	Clients     map[string]struct {
		Version     string  `json:"version"`
		Repo        string  `json:"repo"`
		Branch      string  `json:"branch"`
		TotalPasses int     `json:"total_passes"`
		PassRate    float64 `json:"pass_rate"`
		Simulators  map[string]struct {
			TotalTests int `json:"total_tests"`
		} `json:"simulators"`
	} `json:"clients"`
}

func cmdPasses(group string, args []string, jsonOut bool) error {
	flags := parseFlags(args)
	params := url.Values{}
	if v := flags["client"]; v != "" {
		params.Set("client", v)
	}
	if v := flags["simulator"]; v != "" {
		params.Set("simulator", v)
	}

	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/passes", group), params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}

	var resp passesResp
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	fmt.Printf("%s PASSES — %d total (%s pass rate)\n\n", strings.ToUpper(group), resp.TotalPasses, roundRate(resp.PassRate))

	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "CLIENT\tVERSION\tPASSES\tTOTAL\tPASS RATE\tREPO\tBRANCH")
	fmt.Fprintln(w, "------\t-------\t------\t-----\t---------\t----\t------")

	type passRow struct {
		name        string
		version     string
		repo        string
		branch      string
		totalPasses int
		passRate    float64
		totalTests  int
	}
	var rows []passRow
	for name, c := range resp.Clients {
		var total int
		for _, s := range c.Simulators {
			total += s.TotalTests
		}
		rows = append(rows, passRow{
			name: name, version: c.Version, repo: c.Repo, branch: c.Branch,
			totalPasses: c.TotalPasses, passRate: c.PassRate, totalTests: total,
		})
	}
	sort.Slice(rows, func(i, j int) bool {
		return rows[i].passRate > rows[j].passRate
	})

	for _, r := range rows {
		fmt.Fprintf(w, "%s\t%s\t%d\t%d\t%s\t%s\t%s\n",
			r.name, r.version, r.totalPasses, r.totalTests, roundRate(r.passRate), r.repo, r.branch)
	}
	w.Flush()
	return nil
}

type testListResp struct {
	Group string `json:"group"`
	Total int    `json:"total"`
	Tests []struct {
		Client           string `json:"client"`
		Simulator        string `json:"simulator"`
		EELSTestModule   string `json:"eels_test_module"`
		EELSTestFunction string `json:"eels_test_function"`
		PytestID         string `json:"pytest_id"`
		Fork             string `json:"fork"`
		SuiteTestIndex   string `json:"hive_suite_test_index"`
		SuiteFile        string `json:"suite_file"`
		TestURL          string `json:"eels_test_url"`
		HiveURL          string `json:"hive_url"`
	} `json:"tests"`
}

func cmdTests(group string, args []string, jsonOut bool) error {
	flags := parseFlags(args)
	status := flags["status"]
	if status == "" {
		status = "fail"
	}

	endpoint := fmt.Sprintf("/api/v1/groups/%s/tests", group)

	params := url.Values{}
	params.Set("status", status)
	if v := flags["client"]; v != "" {
		params.Set("client", v)
	}
	if v := flags["simulator"]; v != "" {
		params.Set("simulator", v)
	}
	if v := flags["filter"]; v != "" {
		params.Set("filter", v)
	}
	limit := flags["limit"]
	if limit == "" {
		limit = "50"
	}
	params.Set("limit", limit)

	data, err := apiGet(endpoint, params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}

	var resp testListResp
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	label := "FAILING"
	if status == "pass" {
		label = "PASSING"
	}
	fmt.Printf("%s %s TESTS — %d total (showing %d)\n\n", strings.ToUpper(group), label, resp.Total, len(resp.Tests))

	for i, t := range resp.Tests {
		fmt.Printf("%d. %s\n", i+1, t.EELSTestFunction)
		fmt.Printf("   module:    %s\n", t.EELSTestModule)
		fmt.Printf("   client:    %s\n", t.Client)
		fmt.Printf("   simulator: %s\n", t.Simulator)
		fmt.Printf("   fork:      %s\n", t.Fork)
		fmt.Printf("   pytest_id: %s\n", t.PytestID)
		if t.TestURL != "" {
			fmt.Printf("   source:    %s\n", t.TestURL)
		}
		if t.HiveURL != "" {
			fmt.Printf("   hive:      %s\n", t.HiveURL)
		}
		fmt.Println()
	}
	return nil
}

func cmdDetail(args []string, jsonOut bool) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: clive detail <group> --filter=<regex> [--client=X] [--full-log]")
	}
	group := args[0]
	flags := parseFlags(args[1:])

	filter := flags["filter"]
	if filter == "" {
		// Legacy: clive detail <group> <suite_file> <test_index>
		if len(args) >= 3 {
			params := url.Values{}
			if flags["full-log"] == "true" || flags["full_log"] == "true" {
				params.Set("full_log", "true")
			}
			data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/tests/%s/%s", group, args[1], args[2]), params)
			if err != nil {
				return err
			}
			if jsonOut {
				printJSON(data)
				return nil
			}
			return printDetail(data)
		}
		return fmt.Errorf("usage: clive detail <group> --filter=<regex> [--client=X] [--full-log]")
	}

	params := url.Values{"filter": {filter}}
	if v := flags["client"]; v != "" {
		params.Set("client", v)
	}
	if v := flags["simulator"]; v != "" {
		params.Set("simulator", v)
	}
	if flags["full-log"] == "true" || flags["full_log"] == "true" {
		params.Set("full_log", "true")
	}

	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/tests/lookup", group), params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}
	return printDetail(data)
}

func printDetail(data []byte) error {
	var resp struct {
		EELSTestModule   string `json:"eels_test_module"`
		EELSTestFunction string `json:"eels_test_function"`
		PytestID         string `json:"pytest_id"`
		Fork             string `json:"fork"`
		Client           string `json:"client"`
		Pass             bool   `json:"pass"`
		TestURL          string `json:"eels_test_url"`
		HiveURL          string `json:"hive_url"`
		FillCommand      string `json:"fill_command"`
		HiveCommand      string `json:"hive_command"`
		ConsumeCommand   string `json:"consume_command"`
		ErrorLog         string `json:"error_log"`
		DetailLog        string `json:"detail_log"`
		ClientLog        string `json:"client_log"`
	}
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	status := "FAIL"
	if resp.Pass {
		status = "PASS"
	}

	fmt.Printf("TEST DETAIL [%s]\n", status)
	fmt.Printf("====================\n\n")
	fmt.Printf("Function:  %s\n", resp.EELSTestFunction)
	fmt.Printf("Module:    %s\n", resp.EELSTestModule)
	fmt.Printf("Client:    %s\n", resp.Client)
	fmt.Printf("Fork:      %s\n", resp.Fork)
	fmt.Printf("Pytest ID: %s\n", resp.PytestID)
	if resp.TestURL != "" {
		fmt.Printf("Source:    %s\n", resp.TestURL)
	}
	if resp.HiveURL != "" {
		fmt.Printf("Hive:      %s\n", resp.HiveURL)
	}

	fmt.Printf("\nCOMMANDS\n--------\n")
	fmt.Printf("Fill:    %s\n", resp.FillCommand)
	if resp.ConsumeCommand != "" {
		fmt.Printf("Consume: %s\n", resp.ConsumeCommand)
	}
	if resp.HiveCommand != "" {
		fmt.Printf("Hive:    %s\n", resp.HiveCommand)
	}

	if resp.ErrorLog != "" {
		fmt.Printf("\nERROR LOG\n---------\n%s\n", resp.ErrorLog)
	}
	if resp.DetailLog != "" {
		fmt.Printf("\nDETAIL LOG\n----------\n%s\n", resp.DetailLog)
	}
	if resp.ClientLog != "" {
		fmt.Printf("\nCLIENT LOG (tail)\n-----------------\n%s\n", resp.ClientLog)
	}
	return nil
}

type diffClientEntry struct {
	TotalFails int `json:"total_fails"`
}

type diffResp struct {
	Group          string                     `json:"group"`
	FromRun        time.Time                  `json:"from_run"`
	ToRun          time.Time                  `json:"to_run"`
	Regressions    map[string]diffClientEntry `json:"regressions"`
	Fixes          map[string]diffClientEntry `json:"fixes"`
	UnchangedFails map[string]diffClientEntry `json:"unchanged_fails"`
}

func cmdDiff(group string, args []string, jsonOut bool) error {
	// The text view prints only totals, so it asks for no test lists.
	var params url.Values
	if !jsonOut {
		params = url.Values{"counts": {"true"}}
	}
	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/diff", group), params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}

	var resp diffResp
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	fmt.Printf("%s DIFF\n", strings.ToUpper(group))
	fmt.Printf("From: %s\n", resp.FromRun.Format(time.RFC3339))
	fmt.Printf("To:   %s\n\n", resp.ToRun.Format(time.RFC3339))

	printDiffSection("REGRESSIONS (new failures)", resp.Regressions)
	printDiffSection("FIXES (no longer failing)", resp.Fixes)
	printDiffSection("UNCHANGED FAILS", resp.UnchangedFails)
	return nil
}

func printDiffSection(title string, clients map[string]diffClientEntry) {
	total := 0
	for _, c := range clients {
		total += c.TotalFails
	}
	fmt.Printf("%s: %d\n", title, total)
	if total > 0 {
		for name, c := range clients {
			fmt.Printf("  %s: %d\n", name, c.TotalFails)
		}
	}
	fmt.Println()
}

type searchResp struct {
	Group   string `json:"group"`
	Query   string `json:"query"`
	Total   int    `json:"total"`
	Results []struct {
		Client           string `json:"client"`
		Simulator        string `json:"simulator"`
		EELSTestFunction string `json:"eels_test_function"`
		EELSTestModule   string `json:"eels_test_module"`
		PytestID         string `json:"pytest_id"`
		Fork             string `json:"fork"`
		Pass             bool   `json:"pass"`
		SuiteTestIndex   string `json:"hive_suite_test_index"`
		SuiteFile        string `json:"suite_file"`
		TestURL          string `json:"eels_test_url"`
		HiveURL          string `json:"hive_url"`
	} `json:"results"`
}

func cmdSearch(args []string, jsonOut bool) error {
	if len(args) < 2 {
		return fmt.Errorf("usage: clive search <group> <query> [--client=X] [--status=fail|pass] [--limit=N]")
	}
	group := args[0]
	query := args[1]
	flags := parseFlags(args[2:])

	params := url.Values{"q": {query}}
	if v := flags["client"]; v != "" {
		params.Set("client", v)
	}
	if v := flags["simulator"]; v != "" {
		params.Set("simulator", v)
	}
	if v := flags["status"]; v != "" {
		params.Set("status", v)
	}
	if v := flags["limit"]; v != "" {
		params.Set("limit", v)
	}

	data, err := apiGet(fmt.Sprintf("/api/v1/groups/%s/search", group), params)
	if err != nil {
		return err
	}
	if jsonOut {
		printJSON(data)
		return nil
	}

	var resp searchResp
	if err := json.Unmarshal(data, &resp); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}

	fmt.Printf("SEARCH: %q — %d results\n\n", resp.Query, resp.Total)

	for i, r := range resp.Results {
		status := "FAIL"
		if r.Pass {
			status = "PASS"
		}
		fmt.Printf("%d. [%s] %s\n", i+1, status, r.EELSTestFunction)
		fmt.Printf("   client: %s | simulator: %s | fork: %s\n", r.Client, r.Simulator, r.Fork)
		fmt.Printf("   module: %s\n", r.EELSTestModule)
		fmt.Printf("   detail: clive detail %s %s %s\n",
			group, r.SuiteFile, r.SuiteTestIndex)
		if r.TestURL != "" {
			fmt.Printf("   source: %s\n", r.TestURL)
		}
		if r.HiveURL != "" {
			fmt.Printf("   hive:   %s\n", r.HiveURL)
		}
		fmt.Println()
	}
	return nil
}

// --- utilities ---

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
