from services.machine_service import MachineService


service = MachineService()

print("Registered Engines")

print(service.available_engines())