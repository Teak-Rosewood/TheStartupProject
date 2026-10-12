"""The AWS Solutions rule pack must pass: every finding is fixed or acknowledged with a reason."""

import pytest
from test_stacks import make_config
from tsp.infra import AppStage, enable_checks, new_app


def test_aws_solutions_checks_pass():
    app = new_app()
    AppStage(app, "t", config=make_config())
    enable_checks(app)
    app.synth()


def test_unacknowledged_resource_fails_synth():
    import aws_cdk as cdk
    import aws_cdk.aws_s3 as s3

    app = new_app()
    stack = cdk.Stack(app, "x")
    s3.Bucket(stack, "Open")  # no SSL enforcement, no logs: the pack must object
    enable_checks(app)
    with pytest.raises(Exception, match="AwsSolutions"):
        app.synth()
