from tsp.db import run_alembic_env
from tsp_api.models import Base

run_alembic_env(Base.metadata)
