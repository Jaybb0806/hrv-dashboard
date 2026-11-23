#include <algorithm>
#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "NimBLEDevice.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs_flash.h"

#include "config.h"
#include "payload.h"
#include "pmd.h"
#include "sample_buffer.h"

static const char* TAG = "hrv-gw";

static uint32_t nowMs() { return uint32_t(esp_timer_get_time() / 1000); }

struct Slot {
    std::string id;
    NimBLEAddress addr;
    bool haveAddr = false;
    NimBLEClient* client = nullptr;
    NimBLERemoteCharacteristic* ctrl = nullptr;
    bool connected = false;
    bool streaming = false;
    bool ppiOn = false;
    int hr = -1, bat = -1, rssi = 0;
    uint32_t connectedAt = 0, lastData = 0, lastStart = 0, ppiSince = 0, lastHr = 0, nextAttempt = 0;
    uint32_t failures = 0;
    SampleBuffer buf{BUFFER_SAMPLES};
};

static std::mutex g_lock;
static std::vector<Slot*> g_slots;
static bool g_autoClaim = false;
static bool g_wantPpi = true;
static EventGroupHandle_t g_wifi;
static std::atomic<uint32_t> g_lastPostOk{0};
static constexpr EventBits_t WIFI_UP = BIT0;

static Slot* slotById(const std::string& id) {
    for (Slot* s : g_slots)
        if (s->id == id) return s;
    return nullptr;
}

static Slot* slotFor(const NimBLEClient* c) {
    for (Slot* s : g_slots)
        if (s->client == c) return s;
    return nullptr;
}

static Slot* slotFor(NimBLERemoteCharacteristic* ch) {
    return slotFor(ch->getRemoteService()->getClient());
}

static void addSlot(const std::string& id) {
    if (slotById(id) || int(g_slots.size()) >= MAX_SENSORS) return;
    Slot* s = new Slot();
    s->id = id;
    g_slots.push_back(s);
    ESP_LOGI(TAG, "sensor %s assigned (%d/%d)", id.c_str(), int(g_slots.size()), MAX_SENSORS);
}

static void onHeartRate(NimBLERemoteCharacteristic* ch, uint8_t* d, size_t n, bool) {
    int hr = pmd::parseHeartRate(d, n);
    if (hr < 0) return;
    std::lock_guard<std::mutex> l(g_lock);
    Slot* s = slotFor(ch);
    if (!s) return;
    s->lastHr = nowMs();
    s->hr = hr > 0 ? hr : -1;
}

static void onBattery(NimBLERemoteCharacteristic* ch, uint8_t* d, size_t n, bool) {
    if (n < 1) return;
    std::lock_guard<std::mutex> l(g_lock);
    if (Slot* s = slotFor(ch)) s->bat = d[0];
}

static void onControl(NimBLERemoteCharacteristic* ch, uint8_t* d, size_t n, bool) {
    pmd::ControlResponse r = pmd::parseControlResponse(d, n);
    if (!r.valid || r.type != pmd::TYPE_PPI || r.op != pmd::OP_START) return;
    std::lock_guard<std::mutex> l(g_lock);
    Slot* s = slotFor(ch);
    if (!s) return;
    s->streaming = r.status == pmd::STATUS_SUCCESS || r.status == pmd::STATUS_ALREADY_IN_STATE;
    if (s->streaming)
        ESP_LOGI(TAG, "%s PPI stream started", s->id.c_str());
    else
        ESP_LOGW(TAG, "%s PPI start refused: %s (%d)", s->id.c_str(), pmd::statusText(r.status), r.status);
}

static void onData(NimBLERemoteCharacteristic* ch, uint8_t* d, size_t n, bool) {
    pmd::PpiSample samples[40];
    int k = pmd::parsePpiFrame(d, n, samples, 40);
    if (k <= 0) return;
    std::lock_guard<std::mutex> l(g_lock);
    Slot* s = slotFor(ch);
    if (!s) return;
    for (int i = 0; i < k; ++i) s->buf.push(samples[i]);
    s->lastData = nowMs();
    s->streaming = true;
    const pmd::PpiSample& last = samples[k - 1];
    if (last.contact_supported && !last.contact) s->hr = -1;
    else if (last.hr) s->hr = last.hr;
}

class ScanCallbacks : public NimBLEScanCallbacks {
    void onResult(const NimBLEAdvertisedDevice* dev) override {
        std::string id = deviceIdFromName(dev->getName());
        if (id.empty()) return;
        std::lock_guard<std::mutex> l(g_lock);
        if (!slotById(id) && g_autoClaim) addSlot(id);
        Slot* s = slotById(id);
        if (s && !s->connected && !s->haveAddr) {
            s->addr = dev->getAddress();
            s->haveAddr = true;
            ESP_LOGI(TAG, "found %s rssi %d", id.c_str(), dev->getRSSI());
        }
    }
};

class ClientCallbacks : public NimBLEClientCallbacks {
    void onDisconnect(NimBLEClient* c, int reason) override {
        std::lock_guard<std::mutex> l(g_lock);
        Slot* s = slotFor(c);
        if (!s) return;
        ESP_LOGW(TAG, "%s disconnected, reason %d", s->id.c_str(), reason);
        s->connected = s->streaming = false;
        s->ctrl = nullptr;
        s->nextAttempt = nowMs() + 1000;
    }
};

static ScanCallbacks g_scanCallbacks;
static ClientCallbacks g_clientCallbacks;

static bool startPpi(NimBLERemoteCharacteristic* ctrl) {
    uint8_t cmd[2];
    size_t n = pmd::buildStartPpi(cmd);
    return ctrl->writeValue(cmd, n, true);
}

static bool setupSensor(NimBLEClient* c, Slot* s) {
    if (NimBLERemoteService* hrs = c->getService("180D"))
        if (NimBLERemoteCharacteristic* ch = hrs->getCharacteristic("2A37"))
            ch->subscribe(true, onHeartRate);
    if (NimBLERemoteService* bas = c->getService("180F"))
        if (NimBLERemoteCharacteristic* ch = bas->getCharacteristic("2A19")) {
            NimBLEAttValue v = ch->readValue();
            if (v.size()) {
                std::lock_guard<std::mutex> l(g_lock);
                s->bat = v.data()[0];
            }
            if (ch->canNotify()) ch->subscribe(true, onBattery);
        }

    NimBLERemoteService* svc = c->getService(pmd::SERVICE_UUID);
    if (!svc) {
        ESP_LOGE(TAG, "%s has no PMD service", s->id.c_str());
        return false;
    }
    NimBLERemoteCharacteristic* ctrl = svc->getCharacteristic(pmd::CONTROL_UUID);
    NimBLERemoteCharacteristic* data = svc->getCharacteristic(pmd::DATA_UUID);
    if (!ctrl || !data) return false;
    uint8_t cmd[2];
    ctrl->writeValue(cmd, pmd::buildStopPpi(cmd), true);
    if (!ctrl->subscribe(false, onControl) || !data->subscribe(true, onData)) return false;
    bool ppi;
    {
        std::lock_guard<std::mutex> l(g_lock);
        s->ctrl = ctrl;
        ppi = s->ppiOn = g_wantPpi;
        s->ppiSince = s->lastStart = nowMs();
    }
    return !ppi || startPpi(ctrl);
}

static void connectSlot(Slot* s) {
    NimBLEAddress addr;
    NimBLEClient* c;
    {
        std::lock_guard<std::mutex> l(g_lock);
        addr = s->addr;
        c = s->client;
    }
    if (!c) {
        c = NimBLEDevice::createClient();
        if (!c) {
            ESP_LOGE(TAG, "no free BLE client; raise CONFIG_BT_NIMBLE_MAX_CONNECTIONS");
            return;
        }
        c->setClientCallbacks(&g_clientCallbacks, false);
        c->setConnectionParams(24, 40, 0, 400);
        c->setConnectTimeout(5000);
        std::lock_guard<std::mutex> l(g_lock);
        s->client = c;
    }
    {
        std::lock_guard<std::mutex> l(g_lock);
        s->hr = -1;
        s->lastHr = 0;
    }

    ESP_LOGI(TAG, "connecting %s", s->id.c_str());
    bool ok = c->connect(addr) && setupSensor(c, s) && c->isConnected();
    if (!ok && c->isConnected()) c->disconnect();
    int rssi = ok ? c->getRssi() : 0;

    std::lock_guard<std::mutex> l(g_lock);
    if (ok) {
        s->connected = true;
        s->connectedAt = nowMs();
        s->failures = 0;
        s->rssi = rssi;
        ESP_LOGI(TAG, "%s connected, %s", s->id.c_str(), s->ppiOn ? "waiting for PPI" : "HR only");
        return;
    }
    s->failures++;
    s->nextAttempt = nowMs() + std::min<uint32_t>(30000, 2000 * s->failures);
    if (s->failures % 3 == 0) s->haveAddr = false;
    ESP_LOGW(TAG, "%s connect failed (%u)", s->id.c_str(), unsigned(s->failures));
}

static void maintainLinks() {
    struct Action { Slot* s; NimBLEClient* c; NimBLERemoteCharacteristic* ctrl; int what; };
    enum { RSSI, START, STOP, RESTART, DROP_PPI, DROP_HR };
    std::vector<Action> todo;
    uint32_t t = nowMs();
    {
        std::lock_guard<std::mutex> l(g_lock);
        for (Slot* s : g_slots) {
            if (!s->connected) continue;
            int what = RSSI;
            if (int32_t(t - std::max(s->lastHr, s->connectedAt)) > HR_SILENT_MS) {
                what = DROP_HR;
            } else if (s->ppiOn != g_wantPpi) {
                what = g_wantPpi ? START : STOP;
            } else if (s->ppiOn) {
                uint32_t ref = std::max(s->lastData, s->ppiSince + PPI_START_GRACE_MS);
                if (t > ref && t - ref > PPI_DISCONNECT_AFTER_MS) what = DROP_PPI;
                else if (t > ref && t - ref > PPI_RESTART_AFTER_MS && t - s->lastStart > PPI_RESTART_AFTER_MS) what = RESTART;
            }
            if (what == START) s->ppiSince = t;
            if (what == START || what == STOP) s->ppiOn = what == START;
            if (what == START || what == RESTART) s->lastStart = t;
            todo.push_back({s, s->client, s->ctrl, what});
        }
    }
    for (Action& a : todo) {
        uint8_t cmd[2];
        if (a.what == DROP_HR) {
            ESP_LOGW(TAG, "%s sent no HR for %d s, reconnecting", a.s->id.c_str(), HR_SILENT_MS / 1000);
            a.c->disconnect();
        } else if (a.what == DROP_PPI) {
            ESP_LOGW(TAG, "%s sent no PPI for %d s, reconnecting", a.s->id.c_str(), PPI_DISCONNECT_AFTER_MS / 1000);
            a.c->disconnect();
        } else if (a.what == START && a.ctrl) {
            ESP_LOGI(TAG, "%s mode ppi: waiting for PPI", a.s->id.c_str());
            startPpi(a.ctrl);
        } else if (a.what == STOP && a.ctrl) {
            ESP_LOGI(TAG, "%s mode hr: PPI stopped, HR only", a.s->id.c_str());
            a.ctrl->writeValue(cmd, pmd::buildStopPpi(cmd), true);
        } else if (a.what == RESTART && a.ctrl) {
            ESP_LOGW(TAG, "%s PPI quiet, sending start again", a.s->id.c_str());
            a.ctrl->writeValue(cmd, pmd::buildStopPpi(cmd), true);
            startPpi(a.ctrl);
        } else {
            int rssi = a.c->getRssi();
            std::lock_guard<std::mutex> l(g_lock);
            if (rssi) a.s->rssi = rssi;
        }
    }
}

static void bleTask(void*) {
    NimBLEScan* scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(&g_scanCallbacks, false);
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(80);
    uint32_t nextMaintain = 0;

    for (;;) {
        uint32_t t = nowMs();
        Slot* next = nullptr;
        bool needScan = false;
        {
            std::lock_guard<std::mutex> l(g_lock);
            for (Slot* s : g_slots) {
                if (s->connected) continue;
                if (!s->haveAddr) needScan = true;
                else if (!next && int32_t(t - s->nextAttempt) >= 0) next = s;
            }
            if (g_autoClaim && int(g_slots.size()) < MAX_SENSORS) needScan = true;
        }
        if (next) {
            if (scan->isScanning()) scan->stop();
            connectSlot(next);
            continue;
        }
        if (needScan && !scan->isScanning()) scan->start(5000, false, true);
        if (int32_t(t - nextMaintain) >= 0) {
            maintainLinks();
            nextMaintain = t + 5000;
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static void onWifiEvent(void*, esp_event_base_t base, int32_t id, void* data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(g_wifi, WIFI_UP);
        auto* ev = static_cast<wifi_event_sta_disconnected_t*>(data);
        ESP_LOGW(TAG, "WiFi lost (reason %d), reconnecting", ev->reason);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        auto* ev = static_cast<ip_event_got_ip_t*>(data);
        ESP_LOGI(TAG, "WiFi up, ip " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(g_wifi, WIFI_UP);
    }
}

static void wifiInit() {
    g_wifi = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, onWifiEvent, nullptr, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, onWifiEvent, nullptr, nullptr));
    wifi_config_t wc = {};
    std::strncpy(reinterpret_cast<char*>(wc.sta.ssid), WIFI_SSID, sizeof(wc.sta.ssid));
    std::strncpy(reinterpret_cast<char*>(wc.sta.password), WIFI_PASS, sizeof(wc.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static esp_err_t onHttpEvent(esp_http_client_event_t* e) {
    if (e->event_id == HTTP_EVENT_ON_DATA && e->user_data)
        static_cast<std::string*>(e->user_data)->append(static_cast<const char*>(e->data), e->data_len);
    return ESP_OK;
}

static int httpRequest(const std::string& url, esp_http_client_method_t method,
                       const std::string* body, std::string* response) {
    esp_http_client_config_t cfg = {};
    cfg.url = url.c_str();
    cfg.method = method;
    cfg.timeout_ms = 3000;
    cfg.event_handler = onHttpEvent;
    cfg.user_data = response;
    esp_http_client_handle_t h = esp_http_client_init(&cfg);
    if (!h) return -1;
    if (body) {
        esp_http_client_set_header(h, "Content-Type", "application/json");
        esp_http_client_set_post_field(h, body->data(), int(body->size()));
    }
    esp_err_t err = esp_http_client_perform(h);
    int status = err == ESP_OK ? esp_http_client_get_status_code(h) : -1;
    if (err != ESP_OK) ESP_LOGW(TAG, "HTTP %s: %s", url.c_str(), esp_err_to_name(err));
    esp_http_client_cleanup(h);
    return status;
}

static bool loadSensorList() {
    std::string body;
    std::string url = std::string(SERVER_URL) + "/api/gateway/" + GATEWAY_ID + "/sensors";
    if (httpRequest(url, HTTP_METHOD_GET, nullptr, &body) != 200) return false;
    std::vector<std::string> ids = parseIdList(body, MAX_SENSORS);
    std::lock_guard<std::mutex> l(g_lock);
    for (const std::string& id : ids) addSlot(id);
    ESP_LOGI(TAG, "server assigned %d sensors to %s", int(ids.size()), GATEWAY_ID);
    return true;
}

static void postTask(void*) {
    uint32_t seq = 0;
    std::string lastPhase;
    TickType_t wake = xTaskGetTickCount();
    std::vector<std::vector<pmd::PpiSample>> samples;
    std::vector<SensorReport> reports;
    std::vector<std::pair<Slot*, uint64_t>> acks;

    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(POST_INTERVAL_MS));
        if (!(xEventGroupGetBits(g_wifi) & WIFI_UP)) continue;

        samples.clear();
        reports.clear();
        acks.clear();
        {
            std::lock_guard<std::mutex> l(g_lock);
            for (Slot* s : g_slots) {
                if (!s->connected && s->buf.size() == 0) continue;
                samples.emplace_back();
                acks.emplace_back(s, s->buf.peek(samples.back(), MAX_SAMPLES_PER_POST));
                SensorReport r;
                r.id = s->id;
                r.hr = s->connected ? s->hr : -1;
                r.rssi = s->connected ? s->rssi : 0;
                r.bat = s->bat;
                reports.push_back(r);
            }
        }
        for (size_t i = 0; i < reports.size(); ++i) {
            reports[i].ppi = samples[i].data();
            reports[i].n = samples[i].size();
        }

        wifi_ap_record_t ap;
        int wifiRssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
        std::string body = buildPayload(GATEWAY_ID, seq, uint32_t(esp_timer_get_time() / 1000000), reports, wifiRssi);
        std::string response;
        int status = httpRequest(std::string(SERVER_URL) + "/api/ingest", HTTP_METHOD_POST, &body, &response);
        if (status != 200) {
            ESP_LOGW(TAG, "ingest failed (%d), keeping beats for the next try", status);
            continue;
        }
        {
            std::lock_guard<std::mutex> l(g_lock);
            for (auto& a : acks) a.first->buf.ack(a.second);
        }
        g_lastPostOk = nowMs();
        seq++;
        std::string phase = jsonString(response, "phase");
        if (!phase.empty() && phase != lastPhase) {
            ESP_LOGI(TAG, "dashboard phase: %s", phase.c_str());
            lastPhase = phase;
        }
        std::string mode = jsonString(response, "mode");
        if (mode == "ppi" || mode == "hr") {
            bool changed;
            {
                std::lock_guard<std::mutex> l(g_lock);
                changed = g_wantPpi != (mode == "ppi");
                g_wantPpi = mode == "ppi";
            }
            if (changed) ESP_LOGI(TAG, "server asks for mode %s", mode.c_str());
        }
    }
}

static void ledTask(void*) {
    led_strip_config_t sc = {};
    sc.strip_gpio_num = STATUS_LED_GPIO;
    sc.max_leds = 1;
    sc.led_model = LED_MODEL_WS2812;
    sc.color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB;
    led_strip_rmt_config_t rc = {};
    rc.resolution_hz = 10 * 1000 * 1000;
    led_strip_handle_t led;
    if (led_strip_new_rmt_device(&sc, &rc, &led) != ESP_OK) {
        ESP_LOGW(TAG, "no status LED on GPIO %d", STATUS_LED_GPIO);
        vTaskDelete(nullptr);
    }
    for (;;) {
        uint32_t last = g_lastPostOk;
        bool online = (xEventGroupGetBits(g_wifi) & WIFI_UP) && last && nowMs() - last < 5000;
        int assigned, connected = 0;
        {
            std::lock_guard<std::mutex> l(g_lock);
            assigned = int(g_slots.size());
            for (Slot* s : g_slots) connected += s->connected;
        }
        if (!online) led_strip_set_pixel(led, 0, 24, 0, 0);
        else if (assigned == 0 || connected < assigned) led_strip_set_pixel(led, 0, 20, 12, 0);
        else led_strip_set_pixel(led, 0, 0, 20, 0);
        led_strip_refresh(led);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

extern "C" void app_main() {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_LOGI(TAG, "gateway %s -> %s", GATEWAY_ID, SERVER_URL);

    wifiInit();
    if (STATUS_LED_GPIO >= 0) xTaskCreate(ledTask, "led", 3072, nullptr, 2, nullptr);
    NimBLEDevice::init(std::string("hrv-") + GATEWAY_ID);
    NimBLEDevice::setMTU(232);

    bool canGuess = FALLBACK_SENSORS[0] || AUTO_CLAIM;
    bool listed = false;
    for (int i = 0; !listed && !(canGuess && i >= 5); ++i) {
        if (xEventGroupWaitBits(g_wifi, WIFI_UP, pdFALSE, pdTRUE, pdMS_TO_TICKS(15000)) & WIFI_UP)
            listed = loadSensorList();
        if (!listed) {
            if (!canGuess) ESP_LOGW(TAG, "no sensor list from the server yet, retrying");
            vTaskDelay(pdMS_TO_TICKS(3000));
        }
    }
    {
        std::lock_guard<std::mutex> l(g_lock);
        if (!listed)
            for (const char* const* id = FALLBACK_SENSORS; *id; ++id) addSlot(*id);
        g_autoClaim = AUTO_CLAIM && g_slots.empty();
        if (g_autoClaim)
            ESP_LOGW(TAG, "no sensor list: claiming the first %d Verity Sense in range", MAX_SENSORS);
    }

    xTaskCreate(bleTask, "ble", 6144, nullptr, 5, nullptr);
    xTaskCreate(postTask, "post", 8192, nullptr, 4, nullptr);
}
