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
- Filtro duro: menos de 25 bids (primero los de menos de 5, después el resto; dentro de cada grupo, los más nuevos primero), presupuesto mínimo >= USD 50 (USD 15/h si es por hora; se convierte con `currency.exchange_rate`), una keyword del perfil, nada de Senior/Lead/UX-UI en el título y al menos un skill de dev (`DEV_SKILLS`). El radar en sí no usa LLM: deja los datos crudos (tabla + descripción completa) en `03_tracking/freelancer_leads.md`.
- `02_automation/outbox.py` redacta con Groq un pitch por candidato usando `01_assets/pitch/giga_prompt_private.txt` **tal cual** como system prompt (nada de prompts fijos con stack forzado) y lo deja en `03_tracking/OUTBOX.md`. Estado en `03_tracking/outbox_state.json`: cada proyecto se redacta una vez; `python outbox.py --sent <id>` lo saca del outbox. El bid se manda a mano.
- `02_automation/radar_watch.py` es el punto de entrada del Programador de Tareas (cada 30 min): radar + outbox + alerta de Telegram (con el pitch) por cada proyecto nuevo con menos de 5 bids. Necesita `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`; ids avisados en `03_tracking/notified.json`.
- Fallback con `reverse-api-engineer` (rae), instalado con `uv tool install reverse-api-engineer` y skill en `.claude/skills/reverse-api-engineer/`. Se usa solo si la API de Freelancer.com empieza a responder 403/429 (el radar lo loguea) o si un portal sin API **permite** el acceso automatizado:
  1. Preflight, sin browser ni costo: `reverse-api-engineer agent -p "fetch active projects with title, budget, bid count and link, including pagination" -u <url> --headless --dry-run`
  2. Captura (cobra tokens de Claude y tarda minutos; reportar `usage.total_cost_usd`): el mismo comando sin `--dry-run`, con `--json-stream > rae-run.ndjson` en background.
  3. Leer el cliente generado (`script_path`) antes de correrlo, y adaptarlo como scraper en `02_automation/scrapers/` con su fixture en `tests/fixtures/`.
- La v0.16 no tiene modo "collector": el equivalente es `agent`, que genera un cliente Python que exporta a JSON/CSV.
- Nunca usar rae contra sitios que prohíben scraping (Workana, Upwork). Los HAR y `scripts/` de rae pueden tener cookies y tokens: no se commitean.
