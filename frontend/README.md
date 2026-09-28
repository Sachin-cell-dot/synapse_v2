# SYNAPSE-WX frontend

React, Vite, and TypeScript dashboard for the frozen SYNAPSE-WX historical hindcast. It presents 3,749 saved district-day records for Karnataka's 31 districts from 1 May through 31 August 2026.

## Run locally

```powershell
cd frontend
& 'C:\Program Files\nodejs\npm.cmd' install
& 'C:\Program Files\nodejs\npm.cmd' run dev
```

The default local address is `http://127.0.0.1:5173/`.

## Audited inputs

- `../outputs/synapse_wx_dashboard_forecasts.csv`
- `../outputs/synapse_wx_dashboard_districts.geojson`

Vite serves the generated `../outputs` directory directly during development and copies it unchanged into the production build. The frontend does not create fallback or sample weather values.

## Interpretation

- GFS, IFS HRES, AIFS, SYNAPSE-WX, adaptive weights, confidence, model agreement, IMD rainfall, and error values all come from the saved hindcast.
- Adaptive weights use only earlier district-date IMD errors and sum to 100%.
- IMD realised rainfall is displayed as post-forecast verification only.
- This is a historical hindcast, not a live operational forecast.
