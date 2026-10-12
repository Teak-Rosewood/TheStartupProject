"""Verify Amazon Cognito access / ID tokens. Cognito owns accounts and passwords; this only checks tokens."""

from .cognito import AuthError, Claims, CognitoVerifier

__all__ = ["AuthError", "Claims", "CognitoVerifier"]
