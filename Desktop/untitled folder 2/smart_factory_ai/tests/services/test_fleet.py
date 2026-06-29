from services.fleet_service import FleetService

ranking = FleetService.get_machine_ranking()

for r in ranking:

    print(r)