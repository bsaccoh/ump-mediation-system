from rest_framework import serializers

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession,
    Finding, Region, Site,
)


class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = ['id', 'code', 'name', 'country']


class DistrictSerializer(serializers.ModelSerializer):
    region_name = serializers.CharField(source='region.name', read_only=True)

    class Meta:
        model = District
        fields = ['id', 'code', 'name', 'region', 'region_name']


class ChiefdomSerializer(serializers.ModelSerializer):
    district_name = serializers.CharField(source='district.name', read_only=True)

    class Meta:
        model = Chiefdom
        fields = ['id', 'code', 'name', 'district', 'district_name']


class SiteSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source='operator.name', read_only=True)
    chiefdom_name = serializers.CharField(source='chiefdom.name', read_only=True)

    class Meta:
        model = Site
        fields = [
            'id', 'site_id', 'operator', 'operator_name', 'name', 'site_type',
            'chiefdom', 'chiefdom_name', 'address',
            'latitude', 'longitude', 'altitude_m',
            'is_active',
        ]


class CellSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source='operator.name', read_only=True)
    site_id = serializers.CharField(source='sector.site.site_id', read_only=True)

    class Meta:
        model = Cell
        fields = [
            'id', 'cell_id', 'operator', 'operator_name', 'site_id',
            'technology', 'mcc', 'mnc', 'lac', 'tac', 'ci', 'eci', 'pci',
            'earfcn', 'cgi', 'ecgi',
            'latitude', 'longitude',
            'is_active',
        ]


class DriveTestSessionSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source='operator.name', read_only=True)
    uploaded_by_name = serializers.CharField(source='uploaded_by.get_full_name', read_only=True)
    file_count = serializers.SerializerMethodField()

    class Meta:
        model = DriveTestSession
        fields = [
            'id', 'session_ref', 'operator', 'operator_name',
            'title', 'test_date', 'test_type', 'status',
            'total_measurements', 'matched_measurements', 'finding_count',
            'uploaded_by', 'uploaded_by_name', 'tester_name',
            'created_at', 'updated_at', 'file_count',
        ]

    def get_file_count(self, obj):
        return obj.files.count()


class DriveTestFileSerializer(serializers.ModelSerializer):
    session_ref = serializers.CharField(source='session.session_ref', read_only=True)

    class Meta:
        model = DriveTestFile
        fields = [
            'id', 'session', 'session_ref',
            'original_filename', 'file_size', 'sha256', 'detected_format',
            'status', 'measurement_count', 'error_message',
            'uploaded_at', 'processing_completed_at',
        ]


class FindingSerializer(serializers.ModelSerializer):
    session_ref = serializers.CharField(source='session.session_ref', read_only=True)
    cell_id = serializers.CharField(source='cell.cell_id', read_only=True)

    class Meta:
        model = Finding
        fields = [
            'id', 'session', 'session_ref', 'finding_type', 'severity',
            'latitude', 'longitude', 'cell', 'cell_id',
            'description', 'measured_value', 'threshold_value',
            'is_resolved', 'resolved_at',
            'created_at',
        ]
