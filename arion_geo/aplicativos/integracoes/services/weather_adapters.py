"""Provider contracts. A daily mean wind is never substituted for a gust/maximum."""
import math
from datetime import timedelta
from django.utils import timezone

from ..clients.public_data import PublicDataError

NASA_ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_PARAMETERS = {"T2M":"temperature_2m_mean", "T2M_MIN":"temperature_2m_min",
                   "T2M_MAX":"temperature_2m_max", "PRECTOTCORR":"precipitation_sum"}


class OpenMeteoAdapter:
    key, model, timezone = "open_meteo", "era5", "America/Sao_Paulo"
    batch_size = 8

    def coordinate(self, point):
        # Land-cell selection and elevation downscaling depend on the requested
        # coordinate. Only identical requests are equivalent for this contract.
        return round(point["latitude"], 6), round(point["longitude"], 6)

    def fetch(self, client, cfg, points, start, end, secret, before_attempt):
        from .weather import VARIABLES, parse_daily
        params = {"latitude":",".join(str(self.coordinate(p)[0]) for p in points),
                  "longitude":",".join(str(self.coordinate(p)[1]) for p in points),
                  "start_date":start.isoformat(), "end_date":end.isoformat(),
                  "daily":",".join(VARIABLES), "wind_speed_unit":"kmh", "timezone":self.timezone, "models":self.model}
        if secret:
            params["apikey"] = secret
        payload = client.get(cfg["endpoint"], params, before_attempt=before_attempt)
        payloads = payload if isinstance(payload, list) else [payload]
        if len(payloads) != len(points):
            raise PublicDataError("Quantidade de células incompatível com o lote solicitado.", code="contract")
        # Validate the whole response before persisting any member of the batch.
        samples = [parse_daily(item, start, end) for item in payloads]
        for index,(point,item,sample) in enumerate(zip(points,payloads,samples)):
            lat,lon=self.coordinate(point)
            if ("location_id" in item and item["location_id"]!=index) or abs(sample["cell"][0]-lat)>1.5 or abs(sample["cell"][1]-lon)>1.5:
                raise PublicDataError("Células fora da ordem ou localização solicitada.",code="contract")
        return samples


class NASAPowerAdapter:
    key, model, timezone, batch_size = "nasa_power", "power_meteorology", "UTC", 1

    def coordinate(self, point):
        # Native meteorological grid: 0.5° latitude × 0.625° longitude.
        return round(round(point["latitude"]/.5)*.5, 6), round(round(point["longitude"]/.625)*.625, 6)

    def fetch(self, client, cfg, points, start, end, secret, before_attempt):
        from .weather import VARIABLES
        if start.year<1981:
            raise PublicDataError("NASA POWER diário disponível a partir de 1981.",code="capability")
        lat, lon = self.coordinate(points[0])
        payload = client.get(NASA_ENDPOINT, {"parameters":",".join(NASA_PARAMETERS), "community":"AG",
            "latitude":lat, "longitude":lon, "start":start.strftime("%Y%m%d"), "end":end.strftime("%Y%m%d"),
            "format":"JSON", "time-standard":"UTC"}, before_attempt=before_attempt)
        try:
            days = [start+timedelta(days=i) for i in range((end-start).days+1)]
            header, parameters = payload["header"], payload["properties"]["parameter"]
            coordinates=payload["geometry"]["coordinates"]
            if header["time_standard"].upper() != "UTC" or header["start"]!=start.strftime("%Y%m%d") or header["end"]!=end.strftime("%Y%m%d") or abs(coordinates[0]-lon)>.0001 or abs(coordinates[1]-lat)>.0001:
                raise ValueError
            daily = {"time":[d.isoformat() for d in days], **{v:[None]*len(days) for v in VARIABLES}}
            fill = float(header["fill_value"])
            for parameter, variable in NASA_PARAMETERS.items():
                unit = payload["parameters"][parameter]["units"]
                if unit not in (("mm/day",) if parameter=="PRECTOTCORR" else ("C",)):
                    raise ValueError
                for i, day in enumerate(days):
                    raw = parameters[parameter][day.strftime("%Y%m%d")]
                    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                        raise ValueError
                    value = None if raw == fill else raw
                    if value is not None and parameter=="PRECTOTCORR" and value<0:
                        raise ValueError
                    daily[variable][i] = value
            return [{"cell":[lat,lon], "daily":daily, "units":dict(VARIABLES),
                     "supported_variables":list(NASA_PARAMETERS.values()),
                     "provider_metadata":{"sources":header.get("sources", []), "title":str(header.get("title", ""))[:200],
                         "time_standard":"UTC", "resolution":"0.5° × 0.625°",
                         "provisional":end+timedelta(days=int(cfg.get("history_revision_days") or 120))>=timezone.localdate()},
                     "missing_capabilities":["wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant", "hourly"]}]
        except (KeyError, TypeError, ValueError, OverflowError):
            raise PublicDataError("Contrato NASA POWER inválido: período, unidades ou referência UTC incompatíveis.", code="contract") from None


ADAPTERS = {adapter.key:adapter for adapter in (OpenMeteoAdapter(), NASAPowerAdapter())}


def get_adapter(cfg):
    try:
        return ADAPTERS[cfg.get("preferred_provider", "open_meteo")]
    except KeyError:
        raise PublicDataError("Fonte sem adaptador de coleta online; use o pipeline CDS separado.", code="configuration") from None
