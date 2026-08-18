// ESP32 wireless sensor node with onboard fusion + direct camera trigger
// Reads sensors, computes a confidence score locally, and the instant it
// crosses threshold, sends an HTTP request straight to the camera board
// (bypassing the laptop entirely for lowest latency).
// Still serves /sensors as JSON so the laptop can log/display readings.

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <DHT.h>

#define PIR_PIN 27
#define SOUND_PIN 34
#define GAS_PIN 35
#define TRIG_PIN 26
#define ECHO_PIN 25
#define DHT_PIN 4

DHT dht(DHT_PIN, DHT22);
WebServer server(80);

// const char* ssid = "B3-New";
// const char* password = "Ejust@1234";
// const char* camera_ip = "172.30.103.215";  // <-- set to your ESP32-CAM's IP
// const char* ssid       = "Staff";
// const char* password   = "Sta@2025";
// const char* camera_ip  = "10.107.12.247";
const char* ssid = "Mr.TO7A";
const char* password = "123456789";
const char* camera_ip = "10.16.55.213";  

const char* NODE_ID = "sensor_node_01";
const char* laptop_ip = "10.16.55.1";
const int laptop_port = 5000;
String resolved_camera_ip = String(camera_ip);

const float TRIGGER_THRESHOLD = 0.6;     // tune based on testing
const unsigned long COOLDOWN_MS = 500000;  // don't re-trigger for 5s after a hit
unsigned long lastTriggerTime = 0;
unsigned long lastHeartbeatTime = 0;
const unsigned long HEARTBEAT_INTERVAL = 300000; // 30 seconds

void resolveCameraIP() {
  HTTPClient http;
  String url = "http://" + String(laptop_ip) + ":" + String(laptop_port) + "/api/v1/nodes";
  http.begin(url);
  http.setTimeout(3000);
  int httpCode = http.GET();
  if (httpCode == 200) {
    String payload = http.getString();
    int camIdx = payload.indexOf("\"node_type\":\"camera\"");
    if (camIdx != -1) {
      int ipIdx = payload.indexOf("\"ip_address\":\"", camIdx - 200);
      if (ipIdx == -1) ipIdx = payload.indexOf("\"ip_address\":\"", camIdx);
      if (ipIdx != -1) {
        int startQuote = ipIdx + 14;
        int endQuote = payload.indexOf("\"", startQuote);
        if (endQuote != -1) {
          resolved_camera_ip = payload.substring(startQuote, endQuote);
          Serial.printf("Dynamically resolved camera IP from backend: %s\n", resolved_camera_ip.c_str());
          http.end();
          return;
        }
      }
    }
  }
  Serial.printf("Failed to resolve camera IP, code: %d. Using fallback: %s\n", httpCode, resolved_camera_ip.c_str());
  http.end();
}

void registerNode() {
  HTTPClient http;
  String url = "http://" + String(laptop_ip) + ":" + String(laptop_port) + "/api/v1/nodes/register";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3000);
  String payload = "{\"node_id\":\"" + String(NODE_ID) + "\",\"node_type\":\"sensor\",\"ip_address\":\"" + WiFi.localIP().toString() + "\"}";
  int httpCode = http.POST(payload);
  Serial.printf("Sensor node registration response: %d\n", httpCode);
  http.end();
}


long readUltrasonicCM() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return -1;
  return duration * 0.0343 / 2;
}

// Simple weighted fusion score, 0.0 to 1.0.
// Start simple and tune weights/thresholds after watching real readings.
float computeFusionScore(int pir, int soundLevel, int gasLevel, long distanceCM) {
  float score = 0.0;
  if (pir == HIGH) score += 0.4;                        // motion is a strong signal
  if (soundLevel > 40) score += 0.2;                    // adjust after checking baseline noise
  if (gasLevel > 400) score += 0.2;                     // adjust after MQ-135 warm-up/calibration
  if (distanceCM > 0 && distanceCM < 50) score += 0.2;  // something close by
  return score;
}

void triggerCamera() {
  resolveCameraIP(); // Dynamically look up camera IP
  HTTPClient http;
  String url = "http://" + resolved_camera_ip + "/trigger";
  http.begin(url);
  http.setTimeout(3000);  // don't let a slow camera block the sensor loop for long
  int httpCode = http.GET();
  Serial.printf("Trigger sent to %s, camera responded: %d\n", resolved_camera_ip.c_str(), httpCode);
  http.end();
}

void handleSensors() {
  int pirState = digitalRead(PIR_PIN);
  int soundLevel = analogRead(SOUND_PIN);
  int gasLevel = analogRead(GAS_PIN);
  long distanceCM = readUltrasonicCM();
  float humidity = dht.readHumidity();
  float tempC = dht.readTemperature();
  float score = computeFusionScore(pirState, soundLevel, gasLevel, distanceCM);

  String json = "{";
  json += "\"node_id\":\"" + String(NODE_ID) + "\",";
  json += "\"pir\":" + String(pirState) + ",";
  json += "\"sound\":" + String(soundLevel) + ",";
  json += "\"gas\":" + String(gasLevel) + ",";
  json += "\"distance_cm\":" + String(distanceCM) + ",";
  json += "\"humidity\":" + String(humidity) + ",";
  json += "\"temp_c\":" + String(tempC) + ",";
  json += "\"score\":" + String(score);
  json += "}";

  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  dht.begin();

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Sensor node ready at http://");
  Serial.println(WiFi.localIP());

  registerNode(); // Register on boot
  lastHeartbeatTime = millis();

  server.on("/sensors", HTTP_GET, handleSensors);
  server.begin();
}

void loop() {
  server.handleClient();

  // Periodic heartbeat registration (every 30s)
  if (millis() - lastHeartbeatTime > HEARTBEAT_INTERVAL) {
    registerNode();
    lastHeartbeatTime = millis();
  }

  // Fast detection loop - this runs independently of the /sensors endpoint,
  // so the trigger decision doesn't wait on the laptop asking for anything.
  int pirState = digitalRead(PIR_PIN);
  int soundLevel = analogRead(SOUND_PIN);
  int gasLevel = analogRead(GAS_PIN);
  long distanceCM = readUltrasonicCM();
  float score = computeFusionScore(pirState, soundLevel, gasLevel, distanceCM);

  if (score >= TRIGGER_THRESHOLD && (millis() - lastTriggerTime > COOLDOWN_MS)) {
    triggerCamera();
    lastTriggerTime = millis();
  }

  delay(10000);  // keep this short - it's the main source of detection latency
}
