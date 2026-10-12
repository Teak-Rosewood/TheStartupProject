"""Bot protection for public forms: the captcha secret is injected, never written into the template."""

from __future__ import annotations

import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import EnvConfig


class Growth(Construct):
    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        self.environment: dict[str, str] = {"CAPTCHA_PROVIDER": config.growth.captcha}
        self.secrets: dict[str, ecs.Secret] = {}
        if config.growth.captcha != "none":
            sec = secretsmanager.Secret.from_secret_name_v2(
                self, "Captcha", f"/{config.prefix}/growth/captcha"
            )
            self.secrets["CAPTCHA_SECRET"] = ecs.Secret.from_secrets_manager(sec, "secret_key")
