"""Auth + consent serialization (PRODUCT.md §7.1, §13.7, §18.3)."""

from typing import Any, cast

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.models import ConsentRecord, User
from apps.profiles.models import Child
from apps.profiles.serializers import ChildSerializer


class UserSerializer(serializers.ModelSerializer):
    """The parent as the client sees them. No permission or staff flags leak out."""

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "locale", "created_at"]
        read_only_fields = fields


class MeSerializer(UserSerializer):
    children = ChildSerializer(many=True, read_only=True)

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, "children"]
        read_only_fields = fields


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = ["email", "password", "display_name", "locale"]
        extra_kwargs = {
            "display_name": {"required": False},
            "locale": {"required": False},
        }

    def validate_email(self, value: str) -> str:
        # Normalize before the uniqueness check so `A@x.com` and `a@x.com` are
        # not two accounts holding two different children's data.
        email = User.objects.normalize_email(value).lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def create(self, validated_data: dict[str, Any]) -> User:
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class SignupResponseSerializer(serializers.Serializer):
    """Response shape for signup — documented for the generated TS client.

    No `refresh` field, deliberately. The refresh token leaves as an `httpOnly`
    cookie (`apps.accounts.cookies`); declaring it here would put it back in the
    generated client's type and invite someone to store it.
    """

    user = UserSerializer()
    access = serializers.CharField()


class AccessTokenSerializer(serializers.Serializer):
    """What login and refresh return: the access token, and nothing else.

    The refresh half of the pair travels in a cookie the client cannot read, so
    this really is the whole body — see `apps.accounts.views`.
    """

    access = serializers.CharField()


class ConsentRecordSerializer(serializers.ModelSerializer):
    """Records one verifiable-consent grant.

    `parent`, `ip` and `granted_at` are all server-set: a consent record whose
    evidence the client could dictate is not evidence.
    """

    child_ref = serializers.PrimaryKeyRelatedField(
        source="child",
        queryset=Child.objects.none(),
        required=False,
        allow_null=True,
        help_text=(
            "Optional child this grant is scoped to. Usually omitted — consent "
            "precedes the child."
        ),
    )
    consent_type = serializers.ChoiceField(
        choices=ConsentRecord.ConsentType.choices,
        default=ConsentRecord.ConsentType.DATA_COLLECTION_V1,
    )
    method = serializers.ChoiceField(
        choices=ConsentRecord.Method.choices,
        default=ConsentRecord.Method.CHECKBOX_ATTESTATION,
    )

    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "child_ref",
            "consent_type",
            "method",
            "granted_at",
            "evidence_ref",
            "revoked_at",
        ]
        read_only_fields = ["id", "granted_at", "revoked_at", "evidence_ref"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Scope `child_ref` to the requesting parent's own children, so an
        # attacker cannot mint a consent record pointing at someone else's child.
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            child_ref = cast(serializers.PrimaryKeyRelatedField, self.fields["child_ref"])
            child_ref.queryset = Child.objects.filter(parent=request.user)

    def validate_consent_type(self, value: str) -> str:
        if value != settings.REQUIRED_CONSENT_TYPE:
            raise serializers.ValidationError(
                f"The current consent version is '{settings.REQUIRED_CONSENT_TYPE}'."
            )
        return value


class PrivacySectionSerializer(serializers.Serializer):
    """One section of the disclosure (§12.8)."""

    id = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()


class PrivacyDisclosureSerializer(serializers.Serializer):
    """The plain-language privacy statement (§12.8, §13.8).

    Served from `apps.accounts.privacy` rather than written into the React page,
    because every sentence in it is a claim about what this server does and the
    two have to change together. See that module.
    """

    version = serializers.CharField(
        help_text="Bumped whenever a claim changes, so a parent can tell what they read.",
    )
    headline = serializers.CharField()
    sections = PrivacySectionSerializer(many=True)


class AccountDeleteSerializer(serializers.Serializer):
    """The password, re-checked before a family's data is erased (§12.8).

    Deletion is the only irreversible action in the product. A valid access token
    is what a tablet left on a kitchen table has; it must not also be enough to
    delete two years of a child's stories. Checking the password makes the person
    at the keyboard prove they are the account holder rather than merely holding
    the account holder's device.
    """

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Your account password. Required — deletion cannot be undone.",
    )

    def validate_password(self, value: str) -> str:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.check_password(value):
            # A ValidationError, not a PermissionDenied: the caller *is*
            # authenticated, and what is wrong is the field they sent. It also
            # keeps the 403 in this API meaning exactly one thing — "not yours".
            raise serializers.ValidationError("That password does not match this account.")
        return value
