"""Serializers that exist only to document infrastructure endpoints in OpenAPI."""

from rest_framework import serializers


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Stable machine-readable error code.")
    message = serializers.CharField(help_text="Human-readable, localised message.")
    fields = serializers.DictField(required=False, help_text="Per-field validation messages.")
    request_id = serializers.CharField(allow_null=True, help_text="Correlation id; quote it when reporting problems.")


class ErrorEnvelopeSerializer(serializers.Serializer):
    error = ErrorBodySerializer()


class LivenessSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Always `ok` when the process is serving requests.")
    service = serializers.CharField()
    version = serializers.CharField()
    commit = serializers.CharField()
    time = serializers.DateTimeField()


class CheckSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "error"])
    latency_ms = serializers.FloatField()
    error = serializers.CharField(required=False, help_text="Exception class name only - never connection details.")


class ReadinessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ready", "not_ready"])
    checks = serializers.DictField(child=CheckSerializer())


class ApiRootSerializer(serializers.Serializer):
    name = serializers.CharField()
    version = serializers.CharField()
    status = serializers.CharField()
