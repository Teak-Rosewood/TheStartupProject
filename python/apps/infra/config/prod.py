"""Production environment. Values marked CHANGE are yours to set; secrets come from the environment."""

import os

from tsp.infra import ComputeConfig, DomainConfig, EnvConfig, GithubConfig


def prod() -> EnvConfig:
    return EnvConfig(
        name="prod",
        account=os.environ.get("CDK_DEFAULT_ACCOUNT", "000000000000"),
        domain=DomainConfig(app="app.example.com", auth="auth.example.com"),  # CHANGE
        github=GithubConfig(owner="your-org", repo="TheStartupProject"),  # CHANGE
        alarm_email="alerts@example.com",  # CHANGE
        budget_usd=50,
        compute=ComputeConfig(),
        image_tag=os.environ.get("TSP_IMAGE_TAG", "bootstrap"),
        postgres_image_tag=os.environ.get("TSP_PG_IMAGE_TAG", "bootstrap"),
        # CI supplies this from a GitHub secret (openssl rand -hex 24). The placeholder is for synth only.
        origin_verify_secret=os.environ.get(
            "TSP_ORIGIN_VERIFY", "synth-only-placeholder-secret-value"
        ),
    )
