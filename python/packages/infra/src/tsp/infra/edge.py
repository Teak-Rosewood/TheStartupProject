"""CloudFront in front of an S3 frontend bucket and the API origin."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_cloudfront as cloudfront
import aws_cdk.aws_cloudfront_origins as origins
import aws_cdk.aws_s3 as s3
from constructs import Construct

from .config import EnvConfig

_PRICE_CLASS = {
    "100": cloudfront.PriceClass.PRICE_CLASS_100,
    "200": cloudfront.PriceClass.PRICE_CLASS_200,
    "All": cloudfront.PriceClass.PRICE_CLASS_ALL,
}

# Unknown extensionless paths serve the single-page app shell. /api is never rewritten.
_SPA_REWRITE = """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.indexOf('/api/') !== 0 && uri.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
""".strip()


def frontend_bucket_name(env: EnvConfig) -> str:
    return f"{env.prefix}-frontend-{env.account}"


class Site(Construct):
    def __init__(
        self, scope: Construct, id: str, *, env: EnvConfig, api_origin_domain: str
    ) -> None:
        super().__init__(scope, id)
        cfg = env.compute

        self.bucket = s3.Bucket(
            self,
            "Frontend",
            bucket_name=frontend_bucket_name(env),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # DNS stays at the registrar: the stack pauses here until you add the validation CNAME.
        self.certificate = acm.Certificate(
            self,
            "Cert",
            domain_name=env.domain.app,
            validation=acm.CertificateValidation.from_dns(),
        )

        spa_rewrite = cloudfront.Function(
            self,
            "SpaRewrite",
            code=cloudfront.FunctionCode.from_inline(_SPA_REWRITE),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )

        api_origin = origins.HttpOrigin(
            api_origin_domain,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            http_port=cfg.container_port,
            custom_headers={"X-Origin-Verify": env.origin_verify_secret},
            read_timeout=cdk.Duration.seconds(60),  # streaming LLM replies need headroom
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Cdn",
            domain_names=[env.domain.app],
            certificate=self.certificate,
            default_root_object="index.html",
            price_class=_PRICE_CLASS[env.cloudfront_price_class],
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=spa_rewrite, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST
                    )
                ],
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                )
            },
        )

        cdk.CfnOutput(self, "DistributionDomain", value=self.distribution.distribution_domain_name)
        cdk.CfnOutput(self, "DistributionId", value=self.distribution.distribution_id)
        cdk.CfnOutput(self, "CertificateArn", value=self.certificate.certificate_arn)
