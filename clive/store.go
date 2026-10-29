package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var errTestNotFound = errors.New("test case not found")

// Store fetches and caches hive test results from the static file server.
type Store struct {
	baseURL string
	groups  []string

	mu       sync.RWMutex
	listings map[string][]TestRun // group -> all listing entries (sorted newest first)
	latest   map[string][]TestRun // group -> latest run per (client, simulator)

	suitesMu sync.RWMutex
	suites   map[string]*Suite // "group/fileName" -> cached suite

	names *interner

	client *http.Client
}

func NewStore(baseURL string, groups []string) *Store {
	s := &Store{
		baseURL:  strings.TrimRight(baseURL, "/"),
		listings: make(map[string][]TestRun),
		latest:   make(map[string][]TestRun),
		suites:   make(map[string]*Suite),
		names:    newInterner(),
		client:   &http.Client{Timeout: 120 * time.Second},
	}
	if len(groups) > 0 {
		s.groups = groups
	}
	return s
}

// Start discovers groups (if not set), performs an initial refresh, then refreshes periodically.
func (s *Store) Start(refreshInterval time.Duration) {
	if len(s.groups) == 0 {
		discovered, err := s.discoverGroups()
		if err != nil {
			log.Fatalf("failed to discover groups: %v", err)
		}
		s.groups = discovered
		log.Printf("discovered groups: %v", s.groups)
	}
	s.refresh()
	go func() {
		ticker := time.NewTicker(refreshInterval)
		defer ticker.Stop()
		for range ticker.C {
			// Re-discover groups on each refresh in case new ones are added.
			if discovered, err := s.discoverGroups(); err == nil && len(discovered) > 0 {
				s.mu.Lock()
				s.groups = discovered
				s.mu.Unlock()
			}
			s.refresh()
		}
	}()
}

// discoverGroups fetches discovery.json from the base URL to find available groups.
func (s *Store) discoverGroups() ([]string, error) {
	url := s.baseURL + "/discovery.json"
	resp, err := s.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("GET %s: status %d", url, resp.StatusCode)
	}

	var entries []struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&entries); err != nil {
		return nil, fmt.Errorf("decode %s: %w", url, err)
	}

	groups := make([]string, 0, len(entries))
	for _, e := range entries {
		if e.Name != "" {
			groups = append(groups, e.Name)
		}
	}
	return groups, nil
}

// refresh reloads every group's listing, in parallel.
func (s *Store) refresh() {
	s.mu.RLock()
	groups := make([]string, len(s.groups))
	copy(groups, s.groups)
	s.mu.RUnlock()

	// Start a new intern table, so names from suites that are evicted below
	// do not stay alive through it.
	s.names.reset()

	var wg sync.WaitGroup
	for _, group := range groups {
		wg.Add(1)
		go func(group string) {
			defer wg.Done()
			s.refreshGroup(group)
		}(group)
	}
	wg.Wait()
}

func (s *Store) refreshGroup(group string) {
	runs, err := s.fetchListing(group)
	if err != nil {
		log.Printf("error fetching listing for %s: %v", group, err)
		return
	}

	sort.Slice(runs, func(i, j int) bool {
		return runs[i].Start.After(runs[j].Start)
	})

	latest := findLatestRuns(runs)

	s.mu.Lock()
	s.listings[group] = runs
	s.latest[group] = latest
	s.mu.Unlock()

	// Pre-fetch the latest runs in parallel: the tests of runs with
	// failures, and only the run metadata of the others.
	var wg sync.WaitGroup
	for _, run := range latest {
		wg.Add(1)
		go func(r TestRun) {
			defer wg.Done()
			if _, err := s.GetSuite(group, r.FileName, r.Fails > 0); err != nil {
				log.Printf("error pre-fetching %s/%s: %v", group, r.FileName, err)
			}
		}(run)
	}
	wg.Wait()

	// Evict cached suites for runs no longer in the listing.
	validFiles := make(map[string]bool, len(runs))
	for _, run := range runs {
		validFiles[group+"/"+run.FileName] = true
	}
	s.suitesMu.Lock()
	for key := range s.suites {
		if strings.HasPrefix(key, group+"/") && !validFiles[key] {
			delete(s.suites, key)
		}
	}
	s.suitesMu.Unlock()

	log.Printf("refreshed %s: %d total runs, %d latest", group, len(runs), len(latest))
}

// findLatestRuns returns the most recent run for each unique (simulator, client) pair.
// Input must be sorted newest-first.
func findLatestRuns(runs []TestRun) []TestRun {
	seen := make(map[string]bool)
	var latest []TestRun
	for _, run := range runs {
		hasNew := false
		for _, client := range run.Clients {
			if !seen[run.Name+"/"+client] {
				hasNew = true
			}
		}
		if hasNew {
			for _, client := range run.Clients {
				seen[run.Name+"/"+client] = true
			}
			latest = append(latest, run)
		}
	}
	return latest
}

func (s *Store) fetchListing(group string) ([]TestRun, error) {
	url := fmt.Sprintf("%s/%s/listing.jsonl", s.baseURL, group)
	resp, err := s.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("GET %s: status %d", url, resp.StatusCode)
	}

	var runs []TestRun
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 1<<20), 1<<20) // 1MB line buffer
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var run TestRun
		if err := json.Unmarshal([]byte(line), &run); err != nil {
			log.Printf("skipping malformed line in %s listing: %v", group, err)
			continue
		}
		runs = append(runs, run)
	}
	return runs, scanner.Err()
}

// GetSuite returns the cached suite, reading it if needed. Without tests it
// reads only the run metadata at the top of the file.
func (s *Store) GetSuite(group, fileName string, withTests bool) (*Suite, error) {
	key := group + "/" + fileName

	s.suitesMu.RLock()
	suite, ok := s.suites[key]
	s.suitesMu.RUnlock()
	if ok && (suite.HasTests || !withTests) {
		return suite, nil
	}

	url := fmt.Sprintf("%s/%s/results/%s", s.baseURL, group, fileName)
	resp, err := s.client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("GET %s: status %d", url, resp.StatusCode)
	}

	suite, err = readSuite(resp.Body, withTests, s.names.get)
	if err != nil {
		return nil, fmt.Errorf("decode %s: %w", url, err)
	}

	s.suitesMu.Lock()
	// Keep a copy with tests that a concurrent read already stored.
	if old, ok := s.suites[key]; ok && old.HasTests && !suite.HasTests {
		suite = old
	} else {
		s.suites[key] = suite
	}
	s.suitesMu.Unlock()

	return suite, nil
}

// readSuite reads a suite file. hive writes the run metadata before the
// tests, so without tests it stops there and the rest is never read.
func readSuite(r io.Reader, withTests bool, intern func(string) string) (*Suite, error) {
	dec := json.NewDecoder(r)
	if err := expectDelim(dec, '{'); err != nil {
		return nil, err
	}
	suite := &Suite{}
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			return nil, err
		}
		switch key {
		case "runMetadata":
			if err := dec.Decode(&suite.RunMetadata); err != nil {
				return nil, err
			}
			if !withTests {
				return suite, nil
			}
		case "testDetailsLog":
			if err := dec.Decode(&suite.TestDetailsLog); err != nil {
				return nil, err
			}
		case "testCases":
			if err := readTests(dec, suite, intern); err != nil {
				return nil, err
			}
		default:
			var skip json.RawMessage
			if err := dec.Decode(&skip); err != nil {
				return nil, err
			}
		}
	}
	return suite, nil
}

// readTests reads the testCases object, keeping per test only its name,
// result, source link and where it is in the file.
func readTests(dec *json.Decoder, suite *Suite, intern func(string) string) error {
	if err := expectDelim(dec, '{'); err != nil {
		return err
	}
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			return err
		}
		id, err := strconv.ParseUint(key.(string), 10, 32)
		if err != nil {
			return fmt.Errorf("test id %q: %w", key, err)
		}
		start := dec.InputOffset()
		var tc struct {
			Name          string `json:"name"`
			Description   string `json:"description"`
			SummaryResult struct {
				Pass bool `json:"pass"`
			} `json:"summaryResult"`
		}
		if err := dec.Decode(&tc); err != nil {
			return err
		}
		suite.Tests = append(suite.Tests, SuiteTest{
			Name:   intern(ExtractPytestID(tc.Name)),
			Source: intern(ExtractSourceURL(tc.Description)),
			Offset: start,
			Size:   int32(dec.InputOffset() - start),
			ID:     uint32(id),
			Pass:   tc.SummaryResult.Pass,
		})
	}
	if err := expectDelim(dec, '}'); err != nil {
		return err
	}
	sort.Slice(suite.Tests, func(i, j int) bool {
		return suite.Tests[i].ID < suite.Tests[j].ID
	})
	suite.HasTests = true
	return nil
}

func expectDelim(dec *json.Decoder, want json.Delim) error {
	tok, err := dec.Token()
	if err != nil {
		return err
	}
	if tok != want {
		return fmt.Errorf("expected %v, got %v", want, tok)
	}
	return nil
}

// GetTestCase reads one test case in full, its description, error and log
// locations, by fetching only its bytes of the suite file.
func (s *Store) GetTestCase(group, fileName string, id uint32) (*TestCase, *Suite, error) {
	suite, err := s.GetSuite(group, fileName, true)
	if err != nil {
		return nil, nil, err
	}
	i := sort.Search(len(suite.Tests), func(i int) bool {
		return suite.Tests[i].ID >= id
	})
	if i == len(suite.Tests) || suite.Tests[i].ID != id {
		return nil, suite, errTestNotFound
	}
	t := suite.Tests[i]

	data, err := s.fetchRange(group, fileName, t.Offset, t.Offset+int64(t.Size))
	if err != nil {
		return nil, suite, err
	}
	// The range starts after the test's key, at the ':' before its value.
	var tc TestCase
	if err := json.Unmarshal(bytes.TrimLeft(data, " \t\r\n:"), &tc); err != nil {
		return nil, suite, fmt.Errorf("decode test %d of %s: %w", id, fileName, err)
	}
	return &tc, suite, nil
}

// TestURL links to a test in the hive UI, which shows its description, error
// and client logs.
func (s *Store) TestURL(group, fileName, testID string) string {
	return fmt.Sprintf("%s/#/test/%s/%s?testnumber=%s",
		s.baseURL, group, strings.TrimSuffix(fileName, ".json"), testID)
}

// FetchLog fetches a log file and returns the bytes in [begin, end).
func (s *Store) FetchLog(group, logPath string, begin, end int64) (string, error) {
	data, err := s.fetchRange(group, logPath, begin, end)
	return string(data), err
}

// fetchRange fetches a results file and returns the bytes in [begin, end),
// or all of it when the range is negative.
func (s *Store) fetchRange(group, path string, begin, end int64) ([]byte, error) {
	url := fmt.Sprintf("%s/%s/results/%s", s.baseURL, group, path)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}

	// Try HTTP Range request for efficiency.
	if begin >= 0 && end > begin {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", begin, end-1))
	}

	resp, err := s.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 && resp.StatusCode != 206 {
		return nil, fmt.Errorf("GET %s: status %d", url, resp.StatusCode)
	}

	const maxSize = 50 << 20 // 50 MB
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxSize))
	if err != nil {
		return nil, err
	}

	// If we got a full response instead of a range, slice manually.
	if resp.StatusCode == 200 && begin >= 0 && end > begin && end <= int64(len(data)) {
		return data[begin:end], nil
	}

	return data, nil
}

func (s *Store) GetLatestRuns(group string) ([]TestRun, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	runs, ok := s.latest[group]
	return runs, ok
}

func (s *Store) GetAllRuns(group string) ([]TestRun, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	runs, ok := s.listings[group]
	return runs, ok
}

func (s *Store) GetGroups() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	groups := make([]string, len(s.groups))
	copy(groups, s.groups)
	return groups
}

// interner returns one shared copy of each string it is given.
type interner struct {
	mu sync.Mutex
	m  map[string]string
}

func newInterner() *interner {
	return &interner{m: make(map[string]string)}
}

func (in *interner) get(s string) string {
	in.mu.Lock()
	defer in.mu.Unlock()
	if shared, ok := in.m[s]; ok {
		return shared
	}
	// Copy it, so a substring does not keep the whole string it was cut
	// from alive.
	s = strings.Clone(s)
	in.m[s] = s
	return s
}

func (in *interner) reset() {
	in.mu.Lock()
	defer in.mu.Unlock()
	in.m = make(map[string]string)
}
