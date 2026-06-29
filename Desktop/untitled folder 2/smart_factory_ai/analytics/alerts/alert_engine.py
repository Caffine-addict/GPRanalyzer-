class AlertEngine:

    def evaluate(self, context):

        alerts = []

        quality = context.quality
        drift = context.drift
        health = context.health
        risk = context.risk

        # ------------------------
        # Risk Alerts
        # ------------------------

        if risk["risk_level"] == "CRITICAL":

            alerts.append({

                "severity": "CRITICAL",

                "category": "RISK",

                "message": "Critical production risk detected."

            })

        elif risk["risk_level"] == "HIGH":

            alerts.append({

                "severity": "HIGH",

                "category": "RISK",

                "message": "High production risk detected."

            })

        # ------------------------
        # Health
        # ------------------------

        if health["score"] < 70:

            alerts.append({

                "severity": "HIGH",

                "category": "HEALTH",

                "message": "Machine health has degraded."

            })

        # ------------------------
        # Quality
        # ------------------------

        if quality["status"] != "HEALTHY":

            alerts.append({

                "severity": "MEDIUM",

                "category": "QUALITY",

                "message": "Quality degradation detected."

            })

        # ------------------------
        # Drift
        # ------------------------

        if drift["drift"]:

            alerts.append({

                "severity": "MEDIUM",

                "category": "DRIFT",

                "message": "Measurement drift detected."

            })

        context.alerts = alerts

        return alerts