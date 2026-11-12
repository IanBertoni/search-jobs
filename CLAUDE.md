# job-search

- Este proyecto es para buscar trabajo remoto (Data & AI Engineer) y generar ingresos freelance.
- El código de automatización debe escribirse en Python, usando TDD (pytest).
- Nunca automatizar el envío de propuestas en plataformas como Workana o Upwork, solo la recolección y scoring de ofertas.
- Mantener el código de scraping y matching dentro de la carpeta 02_automation.

## Estructura

- `01_assets/` — CV (`cv/`) y propuestas base (`pitch/`).
- `02_automation/` — scrapers y matcher (Python + pytest).
- `03_tracking/` — leads y postulaciones.
- `04_repos/` — repos de terceros solo como referencia; no importar código desde acá.

## Notas

- Reglas de ECC en `.claude/rules/` (common + python). Lo que sigue tiene prioridad sobre ellas:
- Para este proyecto, la cobertura mínima de tests es 60%, no 80%. Los scrapers se testean con HTML/JSON guardado en tests/fixtures/, no contra sitios reales. No se requieren tests E2E; alcanzan unit tests y tests de integración básicos. La prioridad es velocidad de iteración sobre rigor de producción.
- Scraping: respetar robots.txt y términos de cada sitio, rate-limit, preferir APIs o RSS públicos.
