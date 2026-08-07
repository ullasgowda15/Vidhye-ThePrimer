"""Health check.

Unauthenticated and dependency-free on purpose: it answers "is the web tier
up?", which is what a load balancer and the frontend's status dot need. Deeper
DB/Redis probes belong in a separate readiness endpoint if we ever need one.
"""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    version = serializers.CharField()


class HealthView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="health",
        summary="Liveness probe",
        description="Returns 200 while the API process is serving requests.",
        responses={200: HealthSerializer},
        auth=[],
    )
    def get(self, request: Request) -> Response:
        return Response(
            {
                "status": "ok",
                "service": settings.SERVICE_NAME,
                "version": settings.SERVICE_VERSION,
            },
            status=status.HTTP_200_OK,
        )
