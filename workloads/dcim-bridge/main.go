// dcim-bridge: writes rack-telemetry snapshots to the shared PVC every 5s with fdatasync.
// First victim of S1 PVC contention; exposes write-latency histogram as ground truth.
package main

import (
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	dir := os.Getenv("DATA_DIR")
	if dir == "" { dir = "/shared/dcim" }
	_ = os.MkdirAll(dir, 0o755)
	hist := prometheus.NewHistogram(prometheus.HistogramOpts{
		Name: "dcim_write_seconds", Help: "snapshot write+fdatasync latency",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2, 5},
	})
	prometheus.MustRegister(hist)
	buf := make([]byte, 4<<20) // 4 MB snapshot
	go func() {
		for range time.Tick(5 * time.Second) {
			t0 := time.Now()
			f, err := os.Create(fmt.Sprintf("%s/snap-%d.bin", dir, time.Now().Unix()%12))
			if err != nil { fmt.Println("write err:", err); continue }
			_, _ = f.Write(buf)
			_ = f.Sync() // fdatasync: the honest latency signal
			_ = f.Close()
			hist.Observe(time.Since(t0).Seconds())
		}
	}()
	http.Handle("/metrics", promhttp.Handler())
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	_ = http.ListenAndServe(":8080", nil)
}
