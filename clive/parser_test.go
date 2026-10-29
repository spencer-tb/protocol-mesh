package main

import "testing"

func TestParseCommit(t *testing.T) {
	tests := []struct {
		client, version, want string
	}{
		{"go-ethereum", "Geth/v1.17.2-unstable-d14677f3-20260305/linux-amd64/go1.24.13", "d14677f3"},
		{"reth", "Reth Version: 1.11.0+b00debcc", "b00debcc"},
		{"besu", "besu/v26.3-develop-f9b20c2/linux-x86_64/openjdk-java-21", "f9b20c2"},
		{"nethermind", "1.37.0-unstable+0f8aeb1c", "0f8aeb1c"},
		{"erigon", "3.4.0-dev-f5199870", "f5199870"},
		{"nimbus-el", "Nimbus/v0.2.2-f946fa21/linux-amd64/Nim-2.2.4", "f946fa21"},
		{"ethrex", "ethrex/v9.0.0-bal-devnet-3-6c4a3e66abcdef12/x86_64-linux", "6c4a3e66abcdef12"},
		// Generic fallback.
		{"unknown-client", "some-thing-abcdef01-rest", "abcdef01"},
		// No commit found.
		{"go-ethereum", "no-commit-here", ""},
		{"unknown-client", "", ""},
	}
	for _, tt := range tests {
		got := ParseCommit(tt.client, tt.version)
		if got != tt.want {
			t.Errorf("ParseCommit(%q, %q) = %q, want %q", tt.client, tt.version, got, tt.want)
		}
	}
}

func TestNormalizeClientName(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"reth_default", "reth"},
		{"besu_default", "besu"},
		{"go-ethereum_default", "go-ethereum"},
		{"go-ethereum_custom", "go-ethereum"},
		{"nimbus-el_default", "nimbus-el"},
		{"nethermind_default", "nethermind"},
		{"erigon_default", "erigon"},
		{"ethrex_default", "ethrex"},
		// No underscore — return as-is.
		{"reth", "reth"},
		// Underscore at start — return as-is (idx == 0).
		{"_weird", "_weird"},
	}
	for _, tt := range tests {
		got := NormalizeClientName(tt.input)
		if got != tt.want {
			t.Errorf("NormalizeClientName(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestSimulatorName(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"eels/consume-engine", "consume-engine"},
		{"eels/consume-rlp", "consume-rlp"},
		{"just-a-name", "just-a-name"},
		{"a/b/c", "c"},
	}
	for _, tt := range tests {
		got := SimulatorName(tt.input)
		if got != tt.want {
			t.Errorf("SimulatorName(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestCleanVersion(t *testing.T) {
	tests := []struct {
		client, raw, want string
	}{
		{"go-ethereum", "Geth/v1.17.2-unstable-d14677f3-20260305/linux-amd64/go1.24.13", "v1.17.2-unstable-d14677f3"},
		{"reth", "Reth Version: 1.11.0+b00debcc", "v1.11.0+b00debcc"},
		{"besu", "besu/v26.3-develop-f9b20c2/linux-x86_64/openjdk-java-21", "v26.3-develop-f9b20c2"},
		{"nethermind", "1.37.0-unstable+0f8aeb1c", "v1.37.0-unstable+0f8aeb1c"},
		{"erigon", "3.4.0-dev-f5199870", "v3.4.0-dev-f5199870"},
		{"nimbus-el", "Nimbus/v0.2.2-f946fa21/linux-amd64/Nim-2.2.4\nmore stuff", "v0.2.2-f946fa21"},
		// ethrex with long commit hash gets truncated.
		{"ethrex", "ethrex/v9.0.0-bal-devnet-3-6c4a3e66abcdef12/x86_64-linux", "v9.0.0-bal-devnet-3-6c4a3e66"},
		// Unknown client — return raw.
		{"unknown", "raw-version-string", "raw-version-string"},
		// Empty.
		{"reth", "", ""},
	}
	for _, tt := range tests {
		got := CleanVersion(tt.client, tt.raw)
		if got != tt.want {
			t.Errorf("CleanVersion(%q, %q) = %q, want %q", tt.client, tt.raw, got, tt.want)
		}
	}
}

func TestSplitTestName(t *testing.T) {
	tests := []struct {
		input    string
		wantMod  string
		wantFunc string
	}{
		{
			"tests/amsterdam/test_file.py::test_something[fork_Amsterdam-engine]-reth_default",
			"tests/amsterdam/test_file.py",
			"test_something",
		},
		{
			"tests/osaka/test_eip7692.py::test_gas_limit",
			"tests/osaka/test_eip7692.py",
			"test_gas_limit",
		},
		// No :: separator.
		{"just_a_module_name", "just_a_module_name", ""},
		// No params bracket.
		{"mod::func", "mod", "func"},
	}
	for _, tt := range tests {
		mod, fn := SplitTestName(tt.input)
		if mod != tt.wantMod || fn != tt.wantFunc {
			t.Errorf("SplitTestName(%q) = (%q, %q), want (%q, %q)", tt.input, mod, fn, tt.wantMod, tt.wantFunc)
		}
	}
}

func TestExtractPytestID(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"mod::func[fork_Amsterdam-engine]-reth_default", "mod::func[fork_Amsterdam-engine]"},
		{"mod::func[params]-go-ethereum_default", "mod::func[params]"},
		// No brackets — return as-is.
		{"mod::func_no_params", "mod::func_no_params"},
	}
	for _, tt := range tests {
		got := ExtractPytestID(tt.input)
		if got != tt.want {
			t.Errorf("ExtractPytestID(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestExtractFork(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"mod::func[fork_Amsterdam-blockchain_test_engine]-client", "Amsterdam"},
		{"mod::func[fork_Osaka-engine]-client", "Osaka"},
		// Fork at end of params (no dash after).
		{"mod::func[fork_Prague]", "Prague"},
		// No fork.
		{"mod::func[no_fork_here]-client", ""},
		{"mod::func", ""},
	}
	for _, tt := range tests {
		got := ExtractFork(tt.input)
		if got != tt.want {
			t.Errorf("ExtractFork(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestExtractSourceURL(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{
			`<a href="https://github.com/ethereum/tests/blob/main/test.py">[source]</a>`,
			"https://github.com/ethereum/tests/blob/main/test.py",
		},
		{"no source link here", ""},
		{"", ""},
	}
	for _, tt := range tests {
		got := ExtractSourceURL(tt.input)
		if got != tt.want {
			t.Errorf("ExtractSourceURL(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestExtractHiveCommand(t *testing.T) {
	got := ExtractHiveCommand(`<p>./hive --sim eels/consume-engine --client reth</p>`)
	want := "./hive --sim eels/consume-engine --client reth"
	if got != want {
		t.Errorf("ExtractHiveCommand got %q, want %q", got, want)
	}
	if got := ExtractHiveCommand("no command here"); got != "" {
		t.Errorf("ExtractHiveCommand(no match) = %q, want empty", got)
	}
}

func TestExtractConsumeCommand(t *testing.T) {
	got := ExtractConsumeCommand(`<p>uv run consume engine --input=fixtures</p>`)
	want := "uv run consume engine --input=fixtures"
	if got != want {
		t.Errorf("ExtractConsumeCommand got %q, want %q", got, want)
	}
	if got := ExtractConsumeCommand("nothing"); got != "" {
		t.Errorf("ExtractConsumeCommand(no match) = %q, want empty", got)
	}
}

func TestCleanHTML(t *testing.T) {
	tests := []struct {
		input, want string
	}{
		{"hello&nbsp;world", "hello world"},
		{"a&amp;b", "a&b"},
		{"&lt;tag&gt;", "<tag>"},
		{"line1<br/>line2", "line1line2"},
		{"line1\\<br/>line2", "line1line2"},
		{"  spaces  ", "spaces"},
	}
	for _, tt := range tests {
		got := cleanHTML(tt.input)
		if got != tt.want {
			t.Errorf("cleanHTML(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestBuildFillCommand(t *testing.T) {
	got := BuildFillCommand("mod::func[params]")
	want := "uv run fill mod::func[params]"
	if got != want {
		t.Errorf("BuildFillCommand = %q, want %q", got, want)
	}
}

func TestExtractFixturesRelease(t *testing.T) {
	meta := &RunMetadata{
		HiveCommand: []string{"./hive", "--sim.buildarg", "fixtures=https://github.com/ethereum/fixtures/releases/download/v1.0"},
	}
	got := ExtractFixturesRelease(meta)
	want := "https://github.com/ethereum/fixtures/releases/download/v1.0"
	if got != want {
		t.Errorf("ExtractFixturesRelease = %q, want %q", got, want)
	}

	// Nil metadata.
	if got := ExtractFixturesRelease(nil); got != "" {
		t.Errorf("ExtractFixturesRelease(nil) = %q, want empty", got)
	}

	// No matching arg.
	meta2 := &RunMetadata{HiveCommand: []string{"./hive", "--sim", "foo"}}
	if got := ExtractFixturesRelease(meta2); got != "" {
		t.Errorf("ExtractFixturesRelease(no match) = %q, want empty", got)
	}
}

func TestTailLines(t *testing.T) {
	tests := []struct {
		input string
		n     int
		want  string
	}{
		{"a\nb\nc\nd\ne", 3, "c\nd\ne"},
		{"a\nb", 5, "a\nb"},
		{"single", 1, "single"},
		{"a\nb\nc", 3, "a\nb\nc"},
	}
	for _, tt := range tests {
		got := tailLines(tt.input, tt.n)
		if got != tt.want {
			t.Errorf("tailLines(%q, %d) = %q, want %q", tt.input, tt.n, got, tt.want)
		}
	}
}

func TestExtractBuildInfo(t *testing.T) {
	config := &ClientConfig{
		Content: &ClientConfigContent{
			Clients: []ClientConfigEntry{
				{Client: "reth", BuildArgs: map[string]string{"github": "paradigmxyz/reth", "tag": "main"}},
			},
		},
	}
	repo, branch := ExtractBuildInfo(config, "reth")
	if repo != "paradigmxyz/reth" || branch != "main" {
		t.Errorf("ExtractBuildInfo(reth) = (%q, %q), want (paradigmxyz/reth, main)", repo, branch)
	}

	// Client not found.
	repo, branch = ExtractBuildInfo(config, "besu")
	if repo != "" || branch != "" {
		t.Errorf("ExtractBuildInfo(besu) = (%q, %q), want empty", repo, branch)
	}

	// Nil config.
	repo, branch = ExtractBuildInfo(nil, "reth")
	if repo != "" || branch != "" {
		t.Errorf("ExtractBuildInfo(nil) = (%q, %q), want empty", repo, branch)
	}
}

func TestIsHex(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"abcdef01", true},
		{"0123456789abcdef", true},
		{"ABCDEF", false},
		{"xyz", false},
		{"", true}, // empty is vacuously true
	}
	for _, tt := range tests {
		got := isHex(tt.input)
		if got != tt.want {
			t.Errorf("isHex(%q) = %v, want %v", tt.input, got, tt.want)
		}
	}
}

func TestValidPathSegment(t *testing.T) {
	tests := []struct {
		input string
		want  bool
	}{
		{"valid-file.json", true},
		{"group-name", true},
		{"", false},
		{".", false},
		{"..", false},
		{"path/with/slash", false},
	}
	for _, tt := range tests {
		got := validPathSegment(tt.input)
		if got != tt.want {
			t.Errorf("validPathSegment(%q) = %v, want %v", tt.input, got, tt.want)
		}
	}
}

func TestFirstClient(t *testing.T) {
	run := TestRun{Clients: []string{"reth_default", "besu_default"}}
	name, key := firstClient(run)
	if name != "reth" || key != "reth_default" {
		t.Errorf("firstClient = (%q, %q), want (reth, reth_default)", name, key)
	}

	// Empty clients.
	empty := TestRun{}
	name, key = firstClient(empty)
	if name != "" || key != "" {
		t.Errorf("firstClient(empty) = (%q, %q), want empty", name, key)
	}
}
