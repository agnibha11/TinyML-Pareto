import serial
import time

class DUT:
    def __init__(self, port, baud=115200, timeout=2):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(2) # Wait for board reset
        
    def _send(self, cmd):
        self.ser.write(f"{cmd}\n".encode('ascii'))
        reply = self.ser.readline().decode('ascii').strip()
        if not reply.startswith("OK"):
            raise RuntimeError(f"Device error on '{cmd}': {reply}")
        return reply
        
    def ping(self):
        return self._send("PING")
        
    def list_models(self):
        return self._send("LIST")
        
    def sel(self, k):
        return self._send(f"SEL {k}")
        
    def run(self, n):
        reply = self._send(f"RUN {n}")
        # Parse 'OK us=...'
        return int(reply.split("=")[1])