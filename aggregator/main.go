// L2 aggregator - P3 (BUILD_GUIDE). Polls Prometheus every interval_s using the
// queries.yaml pack, keeps a 15-min per-pod ring buffer (served at /window to L3),
// and emits schema-conformant anomaly_candidate events on threshold breach
// (stdout JSONL + /events + optional POST to L3). Output conforms to
// schema/event.schema.json (FROZEN v1). D-004: the pack sources only kernel/K8s/eBPF
// signals, never an app's own /metrics.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Sample struct {
	TS    time.Time `json:"ts"`
	Pod   string    `json:"pod"`
	NS    string    `json:"namespace"`
	Sig   string    `json:"signal"`
	Value float64   `json:"value"`
}

type Ring struct {
	mu   sync.RWMutex
	data map[string][]Sample // key: ns/pod/signal, capped at capN
	capN int
}

func (r *Ring) Add(s Sample) {
	r.mu.Lock()
	defer r.mu.Unlock()
	k := s.NS + "/" + s.Pod + "/" + s.Sig
	r.data[k] = append(r.data[k], s)
	if len(r.data[k]) > r.capN {
		r.data[k] = r.data[k][len(r.data[k])-r.capN:]
	}
}

func (r *Ring) series(ns, pod, sig string) []float64 {
	r.mu.RLock()
	defer r.mu.RUnlock()
	k := ns + "/" + pod + "/" + sig
	out := make([]float64, len(r.data[k]))
	for i, s := range r.data[k] {
		out[i] = s.Value
	}
	return out
}

// Event conforms to schema/event.schema.json (FROZEN v1).
type Event struct {
	V         int            `json:"v"`
	TS        string         `json:"ts"`
	Kind      string         `json:"kind"`
	NS        string         `json:"namespace"`
	Pod       string         `json:"pod"`
	Signal    string         `json:"signal"`
	Value     float64        `json:"value"`
	Zscore    float64        `json:"zscore,omitempty"`
	Threshold float64        `json:"threshold,omitempty"`
	WindowS   int            `json:"window_s,omitempty"`
	Context   map[string]any `json:"context,omitempty"`
}

// Pack is the queries.yaml contract (ConfigMap-mounted).
type Pack struct {
	IntervalS  int
	WindowMin  int
	Queries    map[string]string // signal -> PromQL
	order      []string          // preserve file order
	Thresholds map[string]float64
}

// schemaSignals = the event.schema.json signal enum. Events may carry only these.
var schemaSignals = map[string]bool{
	"cpu": true, "mem": true, "net_rx": true, "net_tx": true, "pvc_io_util": true,
	"pvc_capacity": true, "psi_cpu": true, "psi_mem": true, "psi_io": true,
	"restarts": true, "latency_p95": true, "log_error_rate": true,
}

// thresholdFor maps a signal to its directly-comparable rule. Only normalized
// signals get rules in v0; cpu rate needs per-pod limit normalization (deferred,
// LOG-027) so cpu fires via psi_cpu instead. pvc_capacity carries no `pod` label
// (kubelet_volume_stats is PVC-keyed) so it is skipped until a PVC->pod join lands.
func thresholdFor(sig string, p *Pack) (float64, bool) {
	switch sig {
	case "pvc_capacity":
		v, ok := p.Thresholds["pvc_capacity"]
		return v, ok
	case "psi_cpu", "psi_mem", "psi_io":
		v, ok := p.Thresholds["psi_some_avg"]
		return v, ok
	case "latency_p95":
		v, ok := p.Thresholds["latency_p95_s"]
		return v, ok
	}
	return 0, false
}

// loadPack parses the flat queries.yaml without a YAML dependency: `key: value`
// under `queries:` / `thresholds:`; the first colon splits key from value (our
// PromQL values contain no colons).
func loadPack(path string) (*Pack, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	p := &Pack{IntervalS: 5, WindowMin: 15, Queries: map[string]string{}, Thresholds: map[string]float64{}}
	sec := ""
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimRight(sc.Text(), " ")
		if strings.TrimSpace(line) == "" || strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		indented := strings.HasPrefix(line, " ")
		t := strings.TrimSpace(line)
		if i := strings.Index(t, " #"); i >= 0 { // strip inline comment
			t = strings.TrimSpace(t[:i])
		}
		if !indented {
			switch {
			case strings.HasPrefix(t, "queries:"):
				sec = "queries"
			case strings.HasPrefix(t, "thresholds:"):
				sec = "thresholds"
			case strings.HasPrefix(t, "interval_s:"):
				p.IntervalS = atoiDefault(valOf(t), 5)
			case strings.HasPrefix(t, "window_min:"):
				p.WindowMin = atoiDefault(valOf(t), 15)
			default:
				sec = ""
			}
			continue
		}
		k, v, ok := splitKV(t)
		if !ok {
			continue
		}
		switch sec {
		case "queries":
			p.Queries[k] = v
			p.order = append(p.order, k)
		case "thresholds":
			if fv, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil {
				p.Thresholds[k] = fv
			}
		}
	}
	return p, sc.Err()
}

func valOf(s string) string { _, v, _ := splitKV(s); return v }
func splitKV(s string) (string, string, bool) {
	i := strings.Index(s, ":")
	if i < 0 {
		return "", "", false
	}
	return strings.TrimSpace(s[:i]), strings.TrimSpace(s[i+1:]), true
}
func atoiDefault(s string, d int) int {
	if n, err := strconv.Atoi(strings.TrimSpace(s)); err == nil {
		return n
	}
	return d
}

func promQuery(base, q string) ([]Sample, error) {
	resp, err := http.Get(base + "/api/v1/query?query=" + url.QueryEscape(q))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var pr struct {
		Data struct {
			Result []struct {
				Metric map[string]string `json:"metric"`
				Value  [2]any            `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &pr); err != nil {
		return nil, err
	}
	out := make([]Sample, 0, len(pr.Data.Result))
	for _, r := range pr.Data.Result {
		s, ok := r.Value[1].(string)
		if !ok {
			continue
		}
		v, err := strconv.ParseFloat(s, 64)
		if err != nil || math.IsNaN(v) || math.IsInf(v, 0) {
			continue
		}
		out = append(out, Sample{TS: time.Now().UTC(), Pod: r.Metric["pod"], NS: r.Metric["namespace"], Value: v})
	}
	return out, nil
}

// zscore: robust (median + MAD), 0 until we have enough history.
func zscore(hist []float64, v float64) float64 {
	if len(hist) < 12 {
		return 0
	}
	cp := append([]float64(nil), hist...)
	sort.Float64s(cp)
	med := cp[len(cp)/2]
	dev := make([]float64, len(cp))
	for i, x := range cp {
		dev[i] = math.Abs(x - med)
	}
	sort.Float64s(dev)
	mad := dev[len(dev)/2]
	if mad == 0 {
		return 0
	}
	return (v - med) / (1.4826 * mad)
}

// evalEvents runs the pack once: fills the per-pod ring, returns threshold breaches.
func evalEvents(prom string, p *Pack, ring *Ring) []Event {
	var events []Event
	for _, sig := range p.order {
		samples, err := promQuery(prom, p.Queries[sig])
		if err != nil {
			fmt.Fprintln(os.Stderr, "prom err", sig, err)
			continue
		}
		for _, s := range samples {
			if s.Pod == "" { // per-pod vectors only; node/PVC-level signals need a join (v0 TODO)
				continue
			}
			s.Sig = sig
			ring.Add(s)
			if !schemaSignals[sig] {
				continue
			}
			if thr, ok := thresholdFor(sig, p); ok && s.Value > thr {
				events = append(events, Event{
					V: 1, TS: time.Now().UTC().Format(time.RFC3339), Kind: "anomaly_candidate",
					NS: s.NS, Pod: s.Pod, Signal: sig, Value: s.Value,
					Zscore: zscore(ring.series(s.NS, s.Pod, sig), s.Value), Threshold: thr, WindowS: 60,
				})
			}
		}
	}
	return events
}

func main() {
	prom := getenv("PROM_URL", "http://prom-kube-prometheus-stack-prometheus.observability.svc:9090")
	packPath := getenv("QUERIES_FILE", "/etc/aggregator/queries.yaml")
	l3 := os.Getenv("L3_URL") // optional: POST events here
	p, err := loadPack(packPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "load pack:", err)
		os.Exit(1)
	}
	if p.IntervalS <= 0 {
		p.IntervalS = 5
	}
	ringCap := (p.WindowMin * 60) / p.IntervalS // 180 @ 15min/5s
	ring := &Ring{data: map[string][]Sample{}, capN: ringCap}

	var emu sync.RWMutex
	recent := []Event{}
	go func() {
		for range time.Tick(time.Duration(p.IntervalS) * time.Second) {
			for _, ev := range evalEvents(prom, p, ring) {
				b, _ := json.Marshal(ev)
				fmt.Println(string(b)) // JSONL to stdout
				emu.Lock()
				recent = append(recent, ev)
				if len(recent) > 256 {
					recent = recent[len(recent)-256:]
				}
				emu.Unlock()
				if l3 != "" {
					go http.Post(l3+"/events", "application/json", bytes.NewReader(b))
				}
			}
		}
	}()

	http.HandleFunc("/window", func(w http.ResponseWriter, _ *http.Request) {
		ring.mu.RLock()
		defer ring.mu.RUnlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(ring.data)
	})
	http.HandleFunc("/events", func(w http.ResponseWriter, _ *http.Request) {
		emu.RLock()
		defer emu.RUnlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(recent)
	})
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	fmt.Printf("aggregator up on :9000 | %d signals, ring cap %d, prom %s\n", len(p.Queries), ringCap, prom)
	_ = http.ListenAndServe(":9000", nil)
}

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
