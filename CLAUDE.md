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

## Freelance Radar

- Fuente principal: `02_automation/freelancer_radar.py`, que usa la API JSON pública de Freelancer.com (`/api/projects/0.1/projects/active/`). Corre las queries python/scraping/automation/data/rag, espera 3 s entre requests y deduplica por id.
- Filtro duro: menos de 15 bids, presupuesto mínimo >= USD 50 (USD 15/h si es por hora; se convierte con `currency.exchange_rate`), una keyword del perfil y nada de Senior/Lead/UX-UI en el título. Groq (`gpt-oss-120b`) redacta un pitch de 3 líneas, o SKIP si no es trabajo de software pago. Salida: `03_tracking/freelancer_leads.md`. El bid se manda a mano.
- Fallback con `reverse-api-engineer` (rae), instalado con `uv tool install reverse-api-engineer` y skill en `.claude/skills/reverse-api-engineer/`. Se usa solo si la API de Freelancer.com empieza a responder 403/429 (el radar lo loguea) o si un portal sin API **permite** el acceso automatizado:
  1. Preflight, sin browser ni costo: `reverse-api-engineer agent -p "fetch active projects with title, budget, bid count and link, including pagination" -u <url> --headless --dry-run`
  2. Captura (cobra tokens de Claude y tarda minutos; reportar `usage.total_cost_usd`): el mismo comando sin `--dry-run`, con `--json-stream > rae-run.ndjson` en background.
  3. Leer el cliente generado (`script_path`) antes de correrlo, y adaptarlo como scraper en `02_automation/scrapers/` con su fixture en `tests/fixtures/`.
- La v0.16 no tiene modo "collector": el equivalente es `agent`, que genera un cliente Python que exporta a JSON/CSV.
- Nunca usar rae contra sitios que prohíben scraping (Workana, Upwork). Los HAR y `scripts/` de rae pueden tener cookies y tokens: no se commitean.
