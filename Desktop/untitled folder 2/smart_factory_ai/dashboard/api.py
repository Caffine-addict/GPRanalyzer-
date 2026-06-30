import requests


class SmartFactoryAPI:

    def __init__(self):
        self.base_url = "http://127.0.0.1:8000"

    def latest_analysis(self):

        response = requests.get(
            f"{self.base_url}/analysis/latest",
            timeout=5
        )

        response.raise_for_status()

        return response.json()