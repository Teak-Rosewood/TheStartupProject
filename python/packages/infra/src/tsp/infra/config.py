"""One pydantic model describes one environment. Everything else is derived from it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DomainConfig(BaseModel):
    """Names served by this environment. DNS stays at the registrar; see docs/aws/03."""

    app: str = Field(description="Public site, e.g. app.example.com")
    auth: str | None = Field(
        default=None, description="Cognito custom domain, e.g. auth.example.com"
    )


class ComputeConfig(BaseModel):
    instance_type: str = "t4g.small"
    data_volume_gb: int = Field(default=40, ge=10)
    root_volume_gb: int = Field(default=16, ge=8)
    container_port: int = 8000
    # Index into the VPC's AZs. The EBS data volume is AZ-bound, so stage 0 pins the host to one.
    az_index: int = Field(default=0, ge=0)
    # Stage 2: internal ALB + CloudFront VPC origin + multi-AZ ASG. See docs/aws/10.
    load_balancer: bool = False
    api_cpu: int = 512
    api_memory_mib: int = 512


class DatabaseConfig(BaseModel):
    # host: Postgres container on the EC2 host (stage 0). rds / aurora: managed (stage 1 / 4).
    mode: Literal["host", "rds", "aurora"] = "host"
    # Keep the host container running next to a managed database during a migration window.
    keep_host_container: bool = False
    name: str = "app"
    username: str = "app"
    memory_mib: int = 768
    full_backup_retention: int = Field(default=4, ge=1, description="weekly full backups kept")
    rds_instance_class: str = "db.t4g.micro"
    rds_storage_gb: int = Field(default=20, ge=20)


class CacheConfig(BaseModel):
    mode: Literal["host", "elasticache", "none"] = "host"
    maxmemory_mb: int = 256
    memory_mib: int = 320


class AuthConfig(BaseModel):
    # A custom auth domain needs a certificate validated by DNS at your registrar (docs/aws/04).
    custom_domain: bool = False
    # Enable once the matching secret exists in AWS Secrets Manager (docs/aws/04).
    google: bool = False
    apple: bool = False
    # Verified SES sender, e.g. no-reply@example.com. None uses Cognito's built-in sender (50/day).
    ses_from: str | None = None


class GithubConfig(BaseModel):
    owner: str
    repo: str
    environment: str = "prod"


class EnvConfig(BaseModel):
    """A deployable environment (``prod`` today, ``dev`` later)."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,15}$")
    account: str = Field(pattern=r"^\d{12}$")
    region: str = "ap-south-1"
    # CloudFront certificates and the frontend bucket must be in us-east-1.
    edge_region: str = "us-east-1"
    domain: DomainConfig
    compute: ComputeConfig = ComputeConfig()
    database: DatabaseConfig = DatabaseConfig()
    cache: CacheConfig = CacheConfig()
    auth: AuthConfig = AuthConfig()
    github: GithubConfig
    alarm_email: str
    budget_usd: float = Field(default=50, gt=0)
    # Image tag the API task runs; CI passes the commit SHA.
    image_tag: str = "bootstrap"
    postgres_image_tag: str = "bootstrap"
    # Sent by CloudFront and required by the app: defense in depth beside the prefix-list SG.
    origin_verify_secret: str = Field(min_length=24, repr=False)
    cloudfront_price_class: Literal["100", "200", "All"] = "200"

    @field_validator("alarm_email")
    @classmethod
    def _email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("alarm_email must be an email address")
        return v

    @property
    def prefix(self) -> str:
        """Name prefix for every resource of this environment."""
        return f"tsp-{self.name}"
