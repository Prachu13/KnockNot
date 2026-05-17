from __future__ import annotations

from pydantic import BaseModel


class ClientReport(BaseModel):
    mac: str
    rssi: int
    connected_time: int = 0


class APReport(BaseModel):
    ap_id: str
    timestamp: str
    associated_clients: list[ClientReport] = []

    @property
    def all_devices(self) -> list[dict]:
        """All detected client devices with MAC and RSSI."""
        return [{"mac": c.mac, "rssi": c.rssi} for c in self.associated_clients]
