from machine_types.ict.event_loader import ICTEventLoader


loader = ICTEventLoader()

events = loader.load_folder("data/raw/ICT")

print()

print("========================")
print("ICT LOADER")
print("========================")

print("Loaded :", len(events))

print()

print(events[0])

print()

print(events[-1])