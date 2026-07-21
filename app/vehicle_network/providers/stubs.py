from __future__ import annotations


class DisabledProvider:
    """付费或需凭证 Provider 的统一占位实现。"""

    def __init__(self, name: str, reason: str = "未配置凭证"):
        self.name = name
        self.reason = reason

    async def collect(self, *args, **kwargs):
        return {"provider": self.name, "status": "disabled", "reason": self.reason, "records": []}


MarineTrafficProvider = lambda: DisabledProvider("MarineTraffic")
OpenSkyProvider = lambda: DisabledProvider("OpenSky")
AviationEdgeProvider = lambda: DisabledProvider("Aviation Edge")
FlightAwareProvider = lambda: DisabledProvider("FlightAware")
CiriumProvider = lambda: DisabledProvider("Cirium")
NewsApiProvider = lambda: DisabledProvider("NewsAPI")
OfacProvider = lambda: DisabledProvider("OFAC SLS")
UnSanctionsProvider = lambda: DisabledProvider("联合国制裁清单")
EuSanctionsProvider = lambda: DisabledProvider("EU Sanctions Map")
