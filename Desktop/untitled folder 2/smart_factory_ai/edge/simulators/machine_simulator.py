import random
import time
from datetime import datetime

from database.db import SessionLocal
from database.schema import Telemetry


class MachineSimulator:

    def __init__(self, machine_id):

        self.machine_id = machine_id

        self.health_state = "NORMAL"

        self.temperature = 65
        self.vibration = 0.8
        self.current = 4.5
        self.rpm = 1500

        self.cycles = 0

    def update_state(self):

        self.cycles += 1

        if self.cycles < 20:
            self.health_state = "NORMAL"

        elif self.cycles < 35:
            self.health_state = "WARNING"

        elif self.cycles < 45:
            self.health_state = "CRITICAL"

        elif self.cycles < 50:
            self.health_state = "FAILURE"

        else:
            self.health_state = "NORMAL"
            self.cycles = 0

    def generate_data(self):

        self.update_state()

        if self.health_state == "NORMAL":

            self.temperature += random.uniform(-1, 1)
            self.vibration += random.uniform(-0.05, 0.05)
            self.current += random.uniform(-0.1, 0.1)
            self.rpm += random.uniform(-10, 10)

        elif self.health_state == "WARNING":

            self.temperature += random.uniform(0.5, 2)

            self.vibration += random.uniform(0.05, 0.15)

            self.current += random.uniform(0.05, 0.2)

            self.rpm += random.uniform(-15, 15)

        elif self.health_state == "CRITICAL":

            self.temperature += random.uniform(1, 3)

            self.vibration += random.uniform(0.1, 0.3)

            self.current += random.uniform(0.2, 0.4)

            self.rpm += random.uniform(-20, 20)

        elif self.health_state == "FAILURE":

            self.temperature += random.uniform(2, 4)

            self.vibration += random.uniform(0.2, 0.5)

            self.current += random.uniform(0.3, 0.5)

            self.rpm -= random.uniform(20, 50)

        return {
            "timestamp": datetime.utcnow(),
            "machine_id": self.machine_id,

            "temperature": round(self.temperature, 2),

            "vibration": round(self.vibration, 2),

            "current": round(self.current, 2),

            "rpm": round(self.rpm, 2),

            "status": self.health_state
        }

    def save_to_db(self, data):

        session = SessionLocal()

        try:

            record = Telemetry(
                timestamp=data["timestamp"],
                machine_id=data["machine_id"],
                temperature=data["temperature"],
                vibration=data["vibration"],
                current=data["current"],
                rpm=data["rpm"],
                status=data["status"]
            )

            session.add(record)
            session.commit()

        finally:
            session.close()