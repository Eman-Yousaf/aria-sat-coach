import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import demo_mode

if __name__ == "__main__":
    print("Aria SAT Tutor — Demo Mode")
    print("This simulates a full WhatsApp conversation with Aria.\n")
    demo_mode(target_phone=sys.argv[1] if len(sys.argv) > 1 else "15551234567")
