from aws_cdk.assertions import Template
from config.prod import prod
from tsp.infra import AppStage, new_app


def test_prod_keeps_logs_for_180_days_and_synthesizes():
    cfg = prod()
    assert cfg.log_retention_days == 180
    stage = AppStage(new_app(), "prod", config=cfg)
    groups = Template.from_stack(stage.backend).find_resources("AWS::Logs::LogGroup")
    assert groups and all(g["Properties"]["RetentionInDays"] == 180 for g in groups.values())
