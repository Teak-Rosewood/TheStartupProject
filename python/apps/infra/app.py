from config.prod import prod
from tsp.infra import AppStage, new_app

app = new_app()
AppStage(app, "prod", config=prod())
app.synth()
