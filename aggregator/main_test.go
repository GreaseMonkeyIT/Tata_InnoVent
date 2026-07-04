// Golden tests for the L2 aggregator (BUILD_GUIDE P3 step 4). Mock Prometheus via
// httptest, assert pack parse + threshold->schema-conformant event + idle silence.
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

const testPack = `interval_s: 5
window_min: 15
queries:
  psi_io:       rate(container_pressure_io_stalled_seconds_total{namespace=~"factory-.*"}[30s])
thresholds:
  psi_some_avg: 0.20
`

func writePack(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "queries.yaml")
	if err := os.WriteFile(p, []byte(testPack), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func mockProm(value float64) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{"status":"success","data":{"resultType":"vector","result":[{"metric":{"namespace":"factory-data","pod":"cooling-monitor-1"},"value":[1718000000,"%g"]}]}}`, value)
	}))
}

func TestLoadPack(t *testing.T) {
	p, err := loadPack(writePack(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Queries) != 1 || p.Queries["psi_io"] == "" {
		t.Fatalf("queries parse: %+v", p.Queries)
	}
	if p.Thresholds["psi_some_avg"] != 0.20 {
		t.Fatalf("threshold parse: %+v", p.Thresholds)
	}
}

func TestThresholdBreachEmitsSchemaEvent(t *testing.T) {
	p, _ := loadPack(writePack(t))
	srv := mockProm(0.94) // psi_io 0.94 > 0.20
	defer srv.Close()
	ring := &Ring{data: map[string][]Sample{}, capN: 180}
	events := evalEvents(srv.URL, p, ring)
	if len(events) != 1 {
		t.Fatalf("want 1 event, got %d", len(events))
	}
	e := events[0]
	if e.V != 1 || e.Kind != "anomaly_candidate" || e.NS != "factory-data" ||
		e.Pod != "cooling-monitor-1" || e.Signal != "psi_io" || e.Value != 0.94 || e.Threshold != 0.20 {
		b, _ := json.Marshal(e)
		t.Fatalf("event field mismatch: %s", b)
	}
	if !schemaSignals[e.Signal] {
		t.Fatalf("emitted non-schema signal %q", e.Signal)
	}
}

func TestIdleIsSilentButRingFills(t *testing.T) {
	p, _ := loadPack(writePack(t))
	srv := mockProm(0.05) // below threshold
	defer srv.Close()
	ring := &Ring{data: map[string][]Sample{}, capN: 180}
	if ev := evalEvents(srv.URL, p, ring); len(ev) != 0 {
		t.Fatalf("want silence at idle, got %d", len(ev))
	}
	if len(ring.data) == 0 {
		t.Fatal("ring should still fill for /window")
	}
}
