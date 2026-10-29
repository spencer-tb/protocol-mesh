package main

import (
	"regexp"
	"strings"
)

// Version string patterns by client name, used to extract commit hashes.
var commitPatterns = map[string]*regexp.Regexp{
	"go-ethereum": regexp.MustCompile(`Geth/v[\d.]+-\w+-([0-9a-f]{8,})`),
	"reth":        regexp.MustCompile(`(?i)reth.*\+([0-9a-f]{7,})`),
	"besu":        regexp.MustCompile(`besu/v[\w.]+-\w+-([0-9a-f]{7,})`),
	"nethermind":  regexp.MustCompile(`[\d.]+(?:-\w+)?\+([0-9a-f]{7,})`),
	"erigon":      regexp.MustCompile(`[\d.]+-\w+-([0-9a-f]{7,})`),
	"nimbus-el":   regexp.MustCompile(`Nimbus/v[\d.]+-([0-9a-f]{7,})`),
	"ethrex":      regexp.MustCompile(`ethrex/v[\w.-]+-([0-9a-f]{7,})`),
}

// Fallback: find any hex string that looks like a commit hash.
var genericCommitPattern = regexp.MustCompile(`[^0-9a-f]([0-9a-f]{7,40})[^0-9a-f]`)

// ParseCommit extracts the commit hash from a client version string.
func ParseCommit(clientName, version string) string {
	if pat, ok := commitPatterns[clientName]; ok {
		if m := pat.FindStringSubmatch(version); len(m) > 1 {
			return m[1]
		}
	}
	if m := genericCommitPattern.FindStringSubmatch(" " + version + " "); len(m) > 1 {
		return m[1]
	}
	return ""
}

// NormalizeClientName strips the _nametag suffix (e.g., "reth_default" -> "reth").
func NormalizeClientName(key string) string {
	knownHyphenated := []string{"go-ethereum", "nimbus-el"}
	for _, c := range knownHyphenated {
		if strings.HasPrefix(key, c) {
			return c
		}
	}
	if idx := strings.LastIndex(key, "_"); idx > 0 {
		return key[:idx]
	}
	return key
}

// SimulatorName extracts the short simulator name from a suite name.
// e.g., "eels/consume-engine" -> "consume-engine"
func SimulatorName(suiteName string) string {
	if idx := strings.LastIndex(suiteName, "/"); idx >= 0 {
		return suiteName[idx+1:]
	}
	return suiteName
}

// CleanVersion extracts a short, clean version string from the raw client version.
// Raw examples:
//
//	go-ethereum: "Geth/v1.17.2-unstable-d14677f3-20260305/linux-amd64/go1.24.13"  -> "v1.17.2-unstable-d14677f3"
//	reth:        "Reth Version: 1.11.0+b00debcc"                                   -> "v1.11.0+b00debcc"
//	besu:        "besu/v26.3-develop-f9b20c2/linux-x86_64/openjdk-java-21"         -> "v26.3-develop-f9b20c2"
//	nethermind:  "1.37.0-unstable+0f8aeb1c"                                        -> "v1.37.0-unstable+0f8aeb1c"
//	erigon:      "3.4.0-dev-f5199870"                                              -> "v3.4.0-dev-f5199870"
//	nimbus-el:   "Nimbus/v0.2.2-f946fa21/linux-amd64/Nim-2.2.4\n..."               -> "v0.2.2-f946fa21"
//	ethrex:      "ethrex/v9.0.0-bal-devnet-3-6c4a3e66.../x86_64-..."               -> "v9.0.0-bal-devnet-3-6c4a3e66"
var versionPatterns = map[string]*regexp.Regexp{
	"go-ethereum": regexp.MustCompile(`Geth/(v[\d.]+-\w+-[0-9a-f]+)`),
	"reth":        regexp.MustCompile(`(?i)reth\s*(?:version:?\s*)?(\d[\w.+]+)`),
	"besu":        regexp.MustCompile(`besu/(v[\w.]+-\w+-[0-9a-f]+)`),
	"nethermind":  regexp.MustCompile(`^([\d]+\.[\d]+\.[\w.+-]+)`),
	"erigon":      regexp.MustCompile(`^([\d]+\.[\d]+\.[\w.+-]+)`),
	"nimbus-el":   regexp.MustCompile(`Nimbus/(v[\d.]+-[0-9a-f]+)`),
	"ethrex":      regexp.MustCompile(`ethrex/(v[\w.-]+)`),
}

func CleanVersion(clientName, raw string) string {
	// Truncate at first newline.
	if idx := strings.IndexByte(raw, '\n'); idx >= 0 {
		raw = raw[:idx]
	}
	raw = strings.TrimSpace(raw)

	if pat, ok := versionPatterns[clientName]; ok {
		if m := pat.FindStringSubmatch(raw); len(m) > 1 {
			v := m[1]
			// Ensure it starts with "v".
			if len(v) > 0 && v[0] != 'v' {
				v = "v" + v
			}
			// Truncate long ethrex commit to short hash.
			if clientName == "ethrex" {
				if parts := strings.Split(v, "-"); len(parts) > 1 {
					last := parts[len(parts)-1]
					if len(last) > 12 && isHex(last) {
						parts[len(parts)-1] = last[:8]
						v = strings.Join(parts, "-")
					}
				}
			}
			return v
		}
	}
	return raw
}

func isHex(s string) bool {
	for _, c := range s {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

// SplitTestName splits "module::function[params]-client" into module and bare function name.
func SplitTestName(testName string) (module, function string) {
	if idx := strings.Index(testName, "::"); idx >= 0 {
		module = testName[:idx]
		function = testName[idx+2:]
	} else {
		module = testName
	}
	// Strip parametrize args and client suffix: "func[params]-client" -> "func"
	if idx := strings.IndexByte(function, '['); idx >= 0 {
		function = function[:idx]
	}
	return
}

var hiveCommandPattern = regexp.MustCompile(`\./hive --sim[^<]+`)
var consumeCommandPattern = regexp.MustCompile(`uv run consume[^<]+`)

// ExtractSourceURL extracts the [source] link from the test case description
// HTML. It runs on every test as suites load, so it searches strings rather
// than using a regexp.
func ExtractSourceURL(description string) string {
	end := strings.Index(description, `">[source]`)
	if end < 0 {
		return ""
	}
	start := strings.LastIndex(description[:end], `href="`)
	if start < 0 {
		return ""
	}
	url := description[start+len(`href="`) : end]
	if !strings.HasPrefix(url, "https://github.com/") || strings.Contains(url, `"`) {
		return ""
	}
	return url
}

// ExtractHiveCommand extracts the ./hive command from the description.
func ExtractHiveCommand(description string) string {
	if m := hiveCommandPattern.FindString(description); m != "" {
		return cleanHTML(m)
	}
	return ""
}

// ExtractConsumeCommand extracts the uv run consume command from the description.
func ExtractConsumeCommand(description string) string {
	if m := consumeCommandPattern.FindString(description); m != "" {
		return cleanHTML(m)
	}
	return ""
}

// cleanHTML removes HTML entities and tags from a string.
func cleanHTML(s string) string {
	s = strings.ReplaceAll(s, "&nbsp;", " ")
	s = strings.ReplaceAll(s, "&amp;", "&")
	s = strings.ReplaceAll(s, "&lt;", "<")
	s = strings.ReplaceAll(s, "&gt;", ">")
	s = strings.ReplaceAll(s, "\\<br/>", "")
	s = strings.ReplaceAll(s, "<br/>", "")
	return strings.TrimSpace(s)
}

// ExtractPytestID strips the client suffix from the test name to get the pytest node ID.
// e.g. "module::func[params]-go-ethereum_default" -> "module::func[params]"
func ExtractPytestID(testName string) string {
	// The client suffix is appended after the last "]" as "-clientname_nametag"
	if idx := strings.LastIndex(testName, "]"); idx >= 0 {
		return testName[:idx+1]
	}
	return testName
}

// ExtractFork extracts the fork name from the test parameters.
// e.g. "module::func[fork_Amsterdam-blockchain_test_engine-...]-client" -> "Amsterdam"
func ExtractFork(testName string) string {
	if idx := strings.Index(testName, "[fork_"); idx >= 0 {
		rest := testName[idx+6:]
		if end := strings.IndexByte(rest, '-'); end >= 0 {
			return rest[:end]
		}
		if end := strings.IndexByte(rest, ']'); end >= 0 {
			return rest[:end]
		}
	}
	return ""
}

// BuildFillCommand constructs a uv run fill command for the given pytest ID.
func BuildFillCommand(pytestID string) string {
	return "uv run fill " + pytestID
}

var fixturesURLPattern = regexp.MustCompile(`fixtures=(https://[^\s"<]+)`)

// ExtractFixturesRelease extracts the fixtures release URL from the hive command args.
func ExtractFixturesRelease(meta *RunMetadata) string {
	if meta == nil {
		return ""
	}
	for i, arg := range meta.HiveCommand {
		if arg == "--sim.buildarg" && i+1 < len(meta.HiveCommand) {
			if m := fixturesURLPattern.FindStringSubmatch(meta.HiveCommand[i+1]); len(m) > 1 {
				return m[1]
			}
		}
	}
	return ""
}

// tailLines returns the last n lines of s.
func tailLines(s string, n int) string {
	lines := strings.Split(s, "\n")
	if len(lines) <= n {
		return s
	}
	return strings.Join(lines[len(lines)-n:], "\n")
}

// ExtractBuildInfo gets repo and branch from client config build args.
func ExtractBuildInfo(config *ClientConfig, clientName string) (repo, branch string) {
	if config == nil || config.Content == nil {
		return
	}
	for _, entry := range config.Content.Clients {
		if entry.Client == clientName {
			repo = entry.BuildArgs["github"]
			branch = entry.BuildArgs["tag"]
			return
		}
	}
	return
}
