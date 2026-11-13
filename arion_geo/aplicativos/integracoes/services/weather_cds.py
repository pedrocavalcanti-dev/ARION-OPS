"""Bounded CDS acquisition stage; optional tooling is isolated from Django runtime.

No NetCDF is automatically treated as an operational cache. Import requires a
separate validated conversion (UTC, precipitation accumulation and units).
"""
import hashlib
import json
from datetime import date, timedelta

DATASET = "reanalysis-era5-single-levels"


def acquisition_plan(start, end, area):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    north, west, south, east = map(float,area)
    if not (1940<=start.year and start<=end<=date.today()-timedelta(days=5) and (end-start).days<=31):
        raise ValueError("CDS: selecione até 32 dias de histórico disponível.")
    if not (-90<=south<north<=90 and -180<=west<east<=180 and (north-south)*(east-west)<=100):
        raise ValueError("CDS: área inválida ou superior a 100 graus quadrados; divida em grades menores.")
    requests=[]
    current=start
    while current<=end:
        last=min(end,(current.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1))
        requests.append({"product_type":["reanalysis"],"variable":["2m_temperature","total_precipitation","10m_u_component_of_wind","10m_v_component_of_wind","instantaneous_10m_wind_gust"],
            "year":[str(current.year)],"month":[f"{current.month:02}"],"day":[f"{i:02}" for i in range(current.day,last.day+1)],
            "time":[f"{i:02}:00" for i in range(24)],"area":[north,west,south,east],"data_format":"netcdf","download_format":"unarchived"})
        current=last+timedelta(days=1)
    return {"dataset":DATASET,"source":"copernicus_cds","model":"era5","timezone":"UTC","requests":requests,
        "terms_url":"https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels",
        "stage":"acquisition_only", "import_ready":False,
        "conversion_requirements":["Validate NetCDF coordinates, units, calendar and timestamps", "Rain: m to mm; accumulation refers to preceding hour; include adjacent boundary hours", "Temperature: K to °C", "Wind: vector magnitude m/s to km/h; keep mean/max distinct", "Daily UTC cannot be merged with America/Sao_Paulo daily cache", "Persist grid provenance and SHA256 before producing a separate territorial method"]}


def download_plan(plan, directory):
    # Import only on explicit management command invocation. No web/worker GET.
    try:
        import cdsapi
    except ImportError:
        raise ValueError("CDS requer o pacote opcional cdsapi e credencial/aceite do dataset; runtime principal permanece sem dependência.") from None
    directory.mkdir(parents=True,exist_ok=True)
    client=cdsapi.Client(quiet=True,debug=False)
    artifacts=[]
    for index,request in enumerate(plan["requests"]):
        target=directory/f"era5-{index+1}.nc"
        client.retrieve(plan["dataset"],request,str(target))
        digest=hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
        artifacts.append({"file":target.name,"sha256":digest.hexdigest()})
    return {**plan,"artifacts":artifacts}
