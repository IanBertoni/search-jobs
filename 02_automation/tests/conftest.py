import os

# tests must not depend on the developer's local .env (python-dotenv >= 1.2)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
