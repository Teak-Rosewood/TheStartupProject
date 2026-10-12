"""Cognito user pool (Essentials): password, Google, Apple, passkeys and email one-time codes."""

from __future__ import annotations

import aws_cdk as cdk
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_cloudfront as cloudfront  # noqa: F401  (kept for type reference in docs)
import aws_cdk.aws_cognito as cognito
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import EnvConfig


def _secret_ref(name: str, key: str) -> cdk.SecretValue:
    """Value of a JSON key in a Secrets Manager secret created by hand (docs/aws/04)."""
    return cdk.SecretValue.secrets_manager(name, json_field=key)


class UserPool(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: EnvConfig,
        certificate: acm.ICertificate | None = None,
    ) -> None:
        super().__init__(scope, id)
        a = config.auth
        region = cdk.Stack.of(self).region
        app_url = f"https://{config.domain.app}"

        email = (
            cognito.UserPoolEmail.with_ses(from_email=a.ses_from, ses_region=region)
            if a.ses_from
            else cognito.UserPoolEmail.with_cognito()
        )
        domain_prefix = f"{config.prefix}-{config.account}"
        if a.custom_domain and config.domain.auth:
            rp_id = config.domain.auth
        else:
            rp_id = f"{domain_prefix}.auth.{region}.amazoncognito.com"

        self.pool = cognito.UserPool(
            self,
            "Pool",
            user_pool_name=f"{config.prefix}-users",
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=10,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            email=email,
            sign_in_policy=cognito.SignInPolicy(
                allowed_first_auth_factors=cognito.AllowedFirstAuthFactors(
                    password=True, passkey=True, email_otp=True
                )
            ),
            passkey_relying_party_id=rp_id,
            passkey_user_verification=cognito.PasskeyUserVerification.PREFERRED,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False, email=False),
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        providers = [cognito.UserPoolClientIdentityProvider.COGNITO]
        if a.google:
            google = cognito.UserPoolIdentityProviderGoogle(
                self,
                "Google",
                user_pool=self.pool,
                client_id=_secret_ref(f"/{config.prefix}/auth/google", "client_id").unsafe_unwrap(),
                client_secret_value=_secret_ref(f"/{config.prefix}/auth/google", "client_secret"),
                scopes=["openid", "email", "profile"],
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.GOOGLE_EMAIL
                ),
            )
            providers.append(cognito.UserPoolClientIdentityProvider.GOOGLE)
        if a.apple:
            apple_secret = f"/{config.prefix}/auth/apple"
            apple = cognito.UserPoolIdentityProviderApple(
                self,
                "Apple",
                user_pool=self.pool,
                client_id=_secret_ref(apple_secret, "services_id").unsafe_unwrap(),
                team_id=_secret_ref(apple_secret, "team_id").unsafe_unwrap(),
                key_id=_secret_ref(apple_secret, "key_id").unsafe_unwrap(),
                private_key_value=_secret_ref(apple_secret, "private_key"),
                scopes=["email", "name"],
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.APPLE_EMAIL
                ),
            )
            providers.append(cognito.UserPoolClientIdentityProvider.APPLE)

        def client(name: str, callbacks: list[str], logouts: list[str]) -> cognito.UserPoolClient:
            c = self.pool.add_client(
                name,
                user_pool_client_name=f"{config.prefix}-{name.lower()}",
                generate_secret=False,  # public clients use PKCE; there is no secret to leak
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[
                        cognito.OAuthScope.OPENID,
                        cognito.OAuthScope.EMAIL,
                        cognito.OAuthScope.PROFILE,
                    ],
                    callback_urls=callbacks,
                    logout_urls=logouts,
                ),
                supported_identity_providers=providers,
                prevent_user_existence_errors=True,
                access_token_validity=cdk.Duration.hours(1),
                id_token_validity=cdk.Duration.hours(1),
                refresh_token_validity=cdk.Duration.days(30),
                enable_token_revocation=True,
            )
            if a.google:
                c.node.add_dependency(google)
            if a.apple:
                c.node.add_dependency(apple)
            return c

        self.web = client("Web", [f"{app_url}/auth/callback"], [f"{app_url}/"])
        self.mobile = client("Mobile", ["tspapp://auth/callback"], ["tspapp://signout"])

        if a.custom_domain and config.domain.auth and certificate is not None:
            self.domain = self.pool.add_domain(
                "Domain",
                custom_domain=cognito.CustomDomainOptions(
                    domain_name=config.domain.auth, certificate=certificate
                ),
                managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
            )
        else:
            self.domain = self.pool.add_domain(
                "Domain",
                cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
                managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
            )
        for c in (self.web, self.mobile):
            cognito.CfnManagedLoginBranding(
                self,
                f"Branding{c.node.id}",
                user_pool_id=self.pool.user_pool_id,
                client_id=c.user_pool_client_id,
                use_cognito_provided_values=True,
            ).node.add_dependency(self.domain)

        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{self.pool.user_pool_id}"
        self.client_ids = f"{self.web.user_pool_client_id},{self.mobile.user_pool_client_id}"
        self.hosted_login_url = (
            f"https://{config.domain.auth}"
            if a.custom_domain and config.domain.auth
            else f"https://{domain_prefix}.auth.{region}.amazoncognito.com"
        )


STAFF_GROUPS = {
    "support": "Read customer records; disable accounts; sign users out",
    "finance": "Read customer and billing records",
    "superadmin": "Everything, including executing account deletions",
}


class AdminPool(Construct):
    """Staff identity: a separate pool so customers can never hold a staff role.

    Admin-created users only, TOTP required, short sessions. The API's console signs in through
    this pool with a confidential client (secret in Secrets Manager, never in the browser).
    """

    def __init__(self, scope: Construct, id: str, *, config: EnvConfig) -> None:
        super().__init__(scope, id)
        region = cdk.Stack.of(self).region
        app_url = f"https://{config.domain.app}"
        domain_prefix = f"{config.prefix}-{config.account}-staff"

        self.pool = cognito.UserPool(
            self,
            "Pool",
            user_pool_name=f"{config.prefix}-staff",
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=False)
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=14,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(3),
            ),
            account_recovery=cognito.AccountRecovery.NONE,  # an administrator resets staff access
            sign_in_policy=cognito.SignInPolicy(
                allowed_first_auth_factors=cognito.AllowedFirstAuthFactors(password=True)
            ),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False, email=False),
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        for name, description in STAFF_GROUPS.items():
            cognito.CfnUserPoolGroup(
                self,
                f"Group{name.title()}",
                user_pool_id=self.pool.user_pool_id,
                group_name=name,
                description=description,
            )

        callback = f"{app_url}/api/admin/oidc/callback"
        self.client = self.pool.add_client(
            "Console",
            user_pool_client_name=f"{config.prefix}-staff-console",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[callback],
                logout_urls=[f"{app_url}/api/admin/"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            prevent_user_existence_errors=True,
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.hours(8),
            enable_token_revocation=True,
        )
        self.domain = self.pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )
        cognito.CfnManagedLoginBranding(
            self,
            "Branding",
            user_pool_id=self.pool.user_pool_id,
            client_id=self.client.user_pool_client_id,
            use_cognito_provided_values=True,
        ).node.add_dependency(self.domain)

        self.client_secret = secretsmanager.Secret(
            self,
            "ClientSecret",
            description=f"{config.prefix} staff console OAuth client secret",
            secret_string_value=self.client.user_pool_client_secret,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{self.pool.user_pool_id}"
        self.domain_url = f"https://{domain_prefix}.auth.{region}.amazoncognito.com"
        self.external_url = app_url
