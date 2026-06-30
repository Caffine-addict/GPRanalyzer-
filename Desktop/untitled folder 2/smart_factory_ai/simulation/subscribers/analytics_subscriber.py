from simulation.integration.analytics_adapter import AnalyticsAdapter


class AnalyticsSubscriber:

    def __init__(self):

        self.adapter = AnalyticsAdapter()

    def __call__(self, event):

        result = self.adapter.process(event)

        print()

        print("Analytics")

        print(result)