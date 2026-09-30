#include <Arduino.h>

// M0 Sync Pin Contract: D2 is used to trigger the ESP32 energy logger
#define PIN_SYNC_D2 2

void setup() {
    Serial.begin(115200);
    
    // Switch off peripherals to ensure a quiet, deterministic board
    digitalWrite(LED_PWR, LOW); 
    digitalWrite(PIN_ENABLE_SENSORS_3V3, LOW); 
    digitalWrite(PIN_ENABLE_I2C_PULLUP, LOW);
    
    // Configure Sync Pin
    pinMode(PIN_SYNC_D2, OUTPUT);
    digitalWrite(PIN_SYNC_D2, LOW); // LOW at rest
    
    // Enable DWT Cycle Counter for latency measurement
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk; 
    DWT->CYCCNT = 0; 
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk; 
}

void loop() {
    if (Serial.available() > 0) {
        String command = Serial.readStringUntil('\n');
        command.trim();
        
        if (command == "PING") {
            Serial.println("OK bench v0.1"); // Update with git-hash later
        } 
        else if (command == "LIST") {
            Serial.println("OK no models loaded"); // Stub for M1
        }
        else if (command.startsWith("SEL")) {
            Serial.println("OK arena_used=0"); // Stub
        }
        else if (command.startsWith("RUN")) {
            // Extract N runs
            int n = command.substring(4).toInt();
            
            // Sync Pin HIGH: ESP32 starts integrating power
            digitalWrite(PIN_SYNC_D2, HIGH);
            
            uint32_t t0 = DWT->CYCCNT;
            
            // --- INFERENCE LOOP STUB ---
            for(int i = 0; i < n; i++) {
                // Front end + Invoke will go here
                __asm__("nop"); 
            }
            // ---------------------------
            
            uint32_t cycles = DWT->CYCCNT - t0;
            
            // Sync Pin LOW: ESP32 stops measuring
            digitalWrite(PIN_SYNC_D2, LOW);
            
            uint32_t window_us = cycles / 64; // 64 MHz clock
            Serial.print("OK us=");
            Serial.println(window_us);
        }
        else {
            Serial.println("ERR unknown command");
        }
    }
}