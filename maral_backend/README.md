# maral_backend

Django + DRF backend for MaralOnlineShop (Phase 2: project skeleton and core infrastructure — **no business modules yet**).

* Setup, environment, database, workers and Docker guides: [`../docs/setup/`](../docs/setup/INSTALLATION.md)
* Architecture and API conventions: [`../docs/architecture/`](../docs/architecture/SYSTEM_ARCHITECTURE.md), [`../docs/api/API_CONVENTIONS.md`](../docs/api/API_CONVENTIONS.md)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env            # fill in DB_* / SECRET_KEY, then:
python manage.py check_database && python manage.py migrate
python manage.py runserver      # http://localhost:8000/api/docs/
```
