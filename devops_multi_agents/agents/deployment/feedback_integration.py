import logging

class EventNotifier:
    def __init__(self):
        self.logger = logging.getLogger("EventNotifier")

    def notify_incident(self, error: str):
        self.logger.error(f"Notifying Incident Response Agent: {error}")

    def notify_chaos(self, services: list):
        self.logger.warning(f"Notifying Chaos Engineering Agent to pause experiments on failing service.")
