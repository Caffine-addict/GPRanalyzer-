class AnalyticsAdapter:

    def process(self, event):

        return {

            "machine": event.machine,

            "board": event.board_id,

            "health": 100,

            "risk": "LOW",

            "quality": "PASS"

        }