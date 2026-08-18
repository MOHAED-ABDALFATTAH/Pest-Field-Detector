// ESP32-CAM: captures on /trigger (called by the sensor board) and
// immediately POSTs the JPEG straight to the laptop - no polling involved.

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>

#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// const char* ssid       = "B3-New";
// const char* password   = "Ejust@1234";
// const char* laptop_ip  = "172.30.103.106";  // <-- set to your laptop's IP
// const char* ssid       = "Staff";
// const char* password   = "Sta@2025";
// const char* laptop_ip  = "10.107.15.254";
const char* ssid       = "Mr.TO7A";
const char* password   = "123456789";
const char* laptop_ip  = "10.16.55.1";
const int   laptop_port = 5000;

const char* NODE_ID = "cam_node_01";
unsigned long lastHeartbeatTime = 0;
const unsigned long HEARTBEAT_INTERVAL = 300000; // 30 seconds

WebServer server(80);

void registerNode() {
  HTTPClient http;
  String url = "http://" + String(laptop_ip) + ":" + String(laptop_port) + "/api/v1/nodes/register";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3000);
  String payload = "{\"node_id\":\"" + String(NODE_ID) + "\",\"node_type\":\"camera\",\"ip_address\":\"" + WiFi.localIP().toString() + "\"}";
  int httpCode = http.POST(payload);
  Serial.printf("Camera node registration response: %d\n", httpCode);
  http.end();
}


void startCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk  = XCLK_GPIO_NUM;
  config.pin_pclk  = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href  = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn  = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format  = PIXFORMAT_JPEG;
  // Smaller frame = faster capture + faster upload = lower latency.
  // Bump to FRAMESIZE_SVGA later if classification accuracy needs more detail.
  config.frame_size    = FRAMESIZE_VGA; // 640x480
  config.jpeg_quality   = 12;
  config.fb_count       = 1;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
  }
}

void handleTrigger() {
  unsigned long t0 = millis();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Capture failed");
    return;
  }
  unsigned long t1 = millis();

  HTTPClient http;
  String url = "http://" + String(laptop_ip) + ":" + String(laptop_port) + "/api/v1/upload";
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  http.addHeader("X-Node-ID", NODE_ID);
  http.addHeader("X-Trigger-Source", "hardware_node");
  http.setTimeout(3000);
  int httpCode = http.POST(fb->buf, fb->len);
  unsigned long t2 = millis();
  http.end();

  Serial.printf("Capture: %lums, Upload: %lums, HTTP code: %d\n", t1 - t0, t2 - t1, httpCode);

  esp_camera_fb_return(fb);
  server.send(200, "text/plain", httpCode > 0 ? "Sent" : "Send failed");
}

// Kept for manual testing / debugging from a browser
void handleCapture() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Content-Disposition", "inline; filename=capture.jpg");
  server.send_P(200, "image/jpeg", (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  startCamera();

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Camera node ready at http://");
  Serial.println(WiFi.localIP());

  registerNode(); // Register on boot
  lastHeartbeatTime = millis();

  server.on("/trigger", HTTP_GET, handleTrigger);
  server.on("/capture", HTTP_GET, handleCapture);
  server.begin();
}

void loop() {
  server.handleClient();

  // Periodic heartbeat registration (every 30s)
  if (millis() - lastHeartbeatTime > HEARTBEAT_INTERVAL) {
    registerNode();
    lastHeartbeatTime = millis();
  }
}
