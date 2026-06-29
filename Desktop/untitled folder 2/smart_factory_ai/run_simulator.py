from edge.simulators.machine_simulator import MachineSimulator
import time

machines = [
    MachineSimulator("M001"),
    MachineSimulator("M002"),
    MachineSimulator("M003")
]

while True:

    for machine in machines:

        data = machine.generate_data()

        machine.save_to_db(data)

        print(data)

    time.sleep(5)