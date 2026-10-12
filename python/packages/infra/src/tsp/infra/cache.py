"""Valkey: a container on the host (stage 0) or ElastiCache Serverless (stage 1). See docs/aws/07."""

from __future__ import annotations

import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_elasticache as elasticache
from constructs import Construct

from .compute import AppService, Host
from .config import EnvConfig

PORT = 6379


class Cache(Construct):
    # CACHE_URL for the API, or None when there is no cache.
    url: str | None

    def __init__(
        self, scope: Construct, id: str, *, config: EnvConfig, host: Host, vpc: ec2.IVpc
    ) -> None:
        super().__init__(scope, id)
        cache = config.cache
        self.services: list[AppService] = []
        self.url = None

        if cache.mode == "host":
            svc = AppService(
                self,
                "Valkey",
                host=host,
                name=f"{config.prefix}-valkey",
                image=ecs.ContainerImage.from_registry("valkey/valkey:8"),
                port=None,
                cpu=0,
                memory_mib=cache.memory_mib,
                command=[
                    "valkey-server",
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(PORT),
                    "--maxmemory",
                    f"{cache.maxmemory_mb}mb",
                    "--maxmemory-policy",
                    "allkeys-lru",
                    "--save",
                    "",  # a cache: nothing to persist
                    "--appendonly",
                    "no",
                ],
                health_check=["valkey-cli -h 127.0.0.1 ping | grep -q PONG"],
            )
            self.services.append(svc)
            self.url = f"redis://127.0.0.1:{PORT}/0"
        elif cache.mode == "elasticache":
            sg = ec2.SecurityGroup(
                self, "Sg", vpc=vpc, description="Valkey: host only", allow_all_outbound=False
            )
            sg.add_ingress_rule(host.security_group, ec2.Port.tcp(PORT), "from the app host")
            cluster = elasticache.CfnServerlessCache(
                self,
                "Serverless",
                engine="valkey",
                serverless_cache_name=f"{config.prefix}-cache",
                major_engine_version="8",
                security_group_ids=[sg.security_group_id],
                subnet_ids=vpc.select_subnets(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ).subnet_ids,
                cache_usage_limits=elasticache.CfnServerlessCache.CacheUsageLimitsProperty(
                    data_storage=elasticache.CfnServerlessCache.DataStorageProperty(
                        maximum=1, unit="GB"
                    ),
                ),
            )
            # TLS is always on for serverless: rediss://
            self.url = f"rediss://{cluster.attr_endpoint_address}:{PORT}/0"
