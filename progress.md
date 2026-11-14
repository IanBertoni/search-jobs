# Progress

## 2026-11-14 — Freelance Client Finder blindado contra estafas

- Reddit eliminado por restricciones de API (Responsible Builder Policy). Se quitaron el código, los tests, el fixture y las variables `REDDIT_*` de config y `.env.example`.
- Reglas nuevas de GitHub Bounties:
  - Blacklist sobre el nombre del repo y el título del issue: `bounty-plaza`, `escrowed`, `bot-trap`, `honeypot`, `trap-benchmark`.
  - Solo monto explícito en USD (`$500`, `500 USD`) de $50 o más. USDC ya no cuenta.
  - El repo tiene que tener 50 estrellas o más y no estar archivado (`archived:false` en la búsqueda y chequeo en la API).
  - Se descartan los repos con más de 100 issues de bounty abiertos (granjas).
  - Máximo 2 issues por repo.
- Algora: no tiene una API pública que funcione (`docs.algora.io` ya no existe y el endpoint legacy `/api/trpc/bounty.list` siempre devuelve una lista vacía). Sus bounties se toman de GitHub por el label `💎 Bounty`, con el monto en otro label (`$150`). Solo se aceptan si pagan más de $100.
- Pitch: solo nombra tecnologías que aparecen en el post o issue ("I can solve this with [tecnología]"). Ya no agrega Rust, RAG ni FastAPI por defecto.
- Estado de las fuentes: GitHub Bounties y Algora (vía GitHub) activas; HN Seeking freelancer activa (0 clientes en los últimos 3 meses); Reddit, Indie Hackers y X fuera.

## 2026-11-14 — Freelance Client Finder listo (versión anterior)

- `02_automation/freelance_client_finder.py` junta clientes freelance y redacta un pitch por cliente con Groq en `03_tracking/freelance_clients.md`. El envío es manual.
- Fuentes:
  - **GitHub Bounties (nueva):** API pública de búsqueda de issues, sin token. Busca issues abiertos con label `bounty`, `help wanted` o `freelance` en repos Python o Rust, y solo deja los que nombran un monto de $50 o más (`$`, `USD`/`USDC`), con máximo 2 por repo. La palabra "bounty" sola no alcanza: en la primera corrida trajo 95 issues de granjas de bounties y repos trampa para bots.
  - **Reddit:** requiere configuración manual de OAuth (app tipo "script" en reddit.com/prefs/apps, y `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` en `02_automation/.env`). Los pasos están en el docstring del módulo. Sin credenciales, se saltea.
  - **HN "Freelancer? Seeking freelancer?":** posts SEEKING FREELANCER. En los últimos 3 meses hubo 0.
- Descartadas: Indie Hackers (Cloudflare + robots.txt) y X (la búsqueda requiere la API paga).
- Pitch en inglés: menciona Python, FastAPI, RAG y Rust, y el GitHub.
