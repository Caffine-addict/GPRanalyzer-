from simulation.core.factory_loader import FactoryLoader


def test_factory_loader():

    factory = FactoryLoader.load(
        "simulation/config/default_factory.json"
    )

    assert factory.name == "Siemens Demo Factory"

    assert len(factory.lines) == 1

    assert len(factory.lines[0].stations) == 4