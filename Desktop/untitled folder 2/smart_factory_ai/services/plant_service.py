from analytics.orchestrator.analysis_orchestrator import AnalysisOrchestrator


class MachineService:
    """
    Entry point for all machine-level analytics.

    Neither the API nor the dashboard should call
    analytics engines directly.
    """

    def __init__(self):

        self.orchestrator = AnalysisOrchestrator()

    def available_engines(self):

        return self.orchestrator.get_registered_engines()

    def analyze_engine(
        self,
        engine_name,
        *args,
        **kwargs
    ):

        return self.orchestrator.analyze(
            engine_name,
            *args,
            **kwargs
        )

    def analyze_machine(
        self,
        input_map
    ):

        return self.orchestrator.analyze_all(
            input_map
        )