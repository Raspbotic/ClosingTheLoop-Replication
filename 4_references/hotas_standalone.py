import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
import socket
import json
import time

UDP_IP = "127.0.0.1"
UDP_PORT = 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

class HotasStandalone:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        
        if pygame.joystick.get_count() == 0:
            print("No USB joystick detected.")
            self.joystick = None
            return
            
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        self.num_axes = self.joystick.get_numaxes()
        self.num_buttons = self.joystick.get_numbuttons()
        self.button_states = {i: False for i in range(self.num_buttons)}
        
        # Memory variables to prevent axis-spam in the console
        self.last_pitch = 0.0
        self.last_roll = 0.0
        self.last_throttle = 0.0
        
        print(f"HOTAS Connected: {self.joystick.get_name()}")
        print(f"Detected {self.num_buttons} Buttons and {self.num_axes} Axes.\n")

    def read_and_transmit(self):
        if not self.joystick:
            return
            
        pygame.event.pump()
                
        # 1. Read Main Axes with 0.05 Deadzones (As per hotas.py)
        roll_raw = self.joystick.get_axis(0)
        roll = roll_raw if abs(roll_raw) > 0.05 else 0.0
        
        pitch_raw = self.joystick.get_axis(1)
        # Inverted for correct sign: Stick forward = Pitch Down (As per hotas.py)
        pitch = -pitch_raw if abs(pitch_raw) > 0.05 else 0.0
        
        # 2. Throttle Logic (As per hotas.py)
        throttle_raw = self.joystick.get_axis(2) if self.num_axes > 2 else 1.0
        # Optional: You can re-add the throttle_synced logic here if desired.
        throttle = (1.0 - throttle_raw) / 2.0
        
        # 3. Smart Rudder Detection (As per hotas.py)
        axis3 = self.joystick.get_axis(3) if self.num_axes > 3 else 0.0
        axis4 = self.joystick.get_axis(4) if self.num_axes > 4 else 0.0
        yaw_raw = axis3 if abs(axis3) > abs(axis4) else axis4
        yaw = yaw_raw if abs(yaw_raw) > 0.05 else 0.0

        airbrake_event = False
        brake = 0.0
        
        # 4. Read Buttons (As per hotas.py)
        for i in range(self.num_buttons):
            is_pressed = self.joystick.get_button(i)
            
            # Edge detection for Airbrake Toggle (Button 4)
            if is_pressed and not self.button_states[i]:
                if i == 4: 
                    airbrake_event = True
                    print("[CMD] Airbrake Toggle Sent")
            
            # Continuous hold for Brakes (Button 6)
            if is_pressed and i == 6:
                brake = 1.0
                
            self.button_states[i] = is_pressed
            
        # 5. Pack and Transmit (Order required by main_window.py)
        payload = [roll, pitch, yaw, throttle, brake, airbrake_event]
        
        try:
            sock.sendto(json.dumps(payload).encode(), (UDP_IP, UDP_PORT))
        except Exception:
            pass

if __name__ == "__main__":
    print("===========================================")
    print(" Starting HOTAS UDP Transmitter (Port 5005)")
    print("===========================================")
    hotas = HotasStandalone()
    try:
        while True:
            hotas.read_and_transmit()
            time.sleep(1/60.0) # 60Hz update rate
    except KeyboardInterrupt:
        print("\nShutting down HOTAS transmitter.")
        pygame.quit()