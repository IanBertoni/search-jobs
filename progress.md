# Progress

## 2026-11-14 — Freelance Client Finder listo

- `02_automation/freelance_client_finder.py` junta clientes freelance y redacta un pitch por cliente con Groq en `03_tracking/freelance_clients.md`. El envío es manual.
- Fuentes:
  - **GitHub Bounties (nueva):** API pública de búsqueda de issues, sin token. Busca issues abiertos con label `bounty`, `help wanted` o `freelance` en repos Python o Rust, y solo deja los que mencionan un pago en el texto (`$`, `USD`, "bounty").
  - **Reddit:** requiere configuración manual de OAuth (app tipo "script" en reddit.com/prefs/apps, y `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` en `02_automation/.env`). Los pasos están en el docstring del módulo. Sin credenciales, se saltea.
  - **HN "Freelancer? Seeking freelancer?":** posts SEEKING FREELANCER. En los últimos 3 meses hubo 0.
- Descartadas: Indie Hackers (Cloudflare + robots.txt) y X (la búsqueda requiere la API paga).
- Pitch en inglés: menciona Python, FastAPI, RAG y Rust, y el GitHub.
