// plc-gateway: simulates 200 PLC sensor channels publishing to MQTT at 10 Hz.
package main

import (
	"fmt"
	"math"
	"math/rand"
	"os"
	"strconv"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

func env(k, d string) string { if v := os.Getenv(k); v != "" { return v }; return d }
func atoiEnv(k string, d int) int { if v := os.Getenv(k); v != "" { if n, err := strconv.Atoi(v); err == nil { return n } }; return d }

func main() {
	broker := env("MQTT_URL", "tcp://mqtt-broker.factory-core.svc:1883")
	opts := mqtt.NewClientOptions().AddBroker(broker).SetClientID("plc-gateway").SetAutoReconnect(true)
	c := mqtt.NewClient(opts)
	for t := c.Connect(); t.Wait() && t.Error() != nil; t = c.Connect() {
		fmt.Println("mqtt connect retry:", t.Error()); time.Sleep(2 * time.Second)
	}
	periodMs := atoiEnv("PLC_PERIOD_MS", 100) // 100ms=10Hz default; raise to shed DB write volume so timescaledb idles quiet (clean S1 victim)
	channels := atoiEnv("PLC_CHANNELS", 200)
	tick := time.NewTicker(time.Duration(periodMs) * time.Millisecond)
	defer tick.Stop()
	start := time.Now()
	for range tick.C {
		el := time.Since(start).Seconds()
		for ch := 0; ch < channels; ch++ {
			v := 50 + 30*math.Sin(el/30+float64(ch)) + rand.NormFloat64()*2
			c.Publish(fmt.Sprintf("sensors/%d", ch), 1, false, fmt.Sprintf(`{"ch":%d,"v":%.2f,"ts":%d}`, ch, v, time.Now().UnixMilli()))
		}
	}
}
