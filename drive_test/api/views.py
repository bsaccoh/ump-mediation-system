from rest_framework import filters, generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from drive_test.models import (
    Cell, Chiefdom, District, DriveTestFile, DriveTestSession,
    Finding, Region, Site,
)

from .serializers import (
    CellSerializer, ChiefdomSerializer, DistrictSerializer,
    DriveTestFileSerializer, DriveTestSessionSerializer,
    FindingSerializer, RegionSerializer, SiteSerializer,
)


class RegionListView(generics.ListAPIView):
    queryset = Region.objects.all().order_by('name')
    serializer_class = RegionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'code']


class DistrictListView(generics.ListAPIView):
    queryset = District.objects.select_related('region').order_by('name')
    serializer_class = DistrictSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ['region']

    def get_queryset(self):
        qs = super().get_queryset()
        region = self.request.query_params.get('region')
        if region:
            qs = qs.filter(region_id=region)
        return qs


class ChiefdomListView(generics.ListAPIView):
    queryset = Chiefdom.objects.select_related('district__region').order_by('name')
    serializer_class = ChiefdomSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        district = self.request.query_params.get('district')
        if district:
            qs = qs.filter(district_id=district)
        return qs


class SiteListView(generics.ListAPIView):
    serializer_class = SiteSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['site_id', 'name']

    def get_queryset(self):
        qs = Site.objects.select_related('operator', 'chiefdom').order_by('site_id')
        operator = self.request.query_params.get('operator')
        if operator:
            qs = qs.filter(operator_id=operator)
        active = self.request.query_params.get('active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() in ('true', '1', 'yes'))
        return qs


class CellListView(generics.ListAPIView):
    serializer_class = CellSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['cell_id', 'cgi', 'ecgi']

    def get_queryset(self):
        qs = Cell.objects.select_related('operator', 'sector__site').order_by('cell_id')
        operator = self.request.query_params.get('operator')
        technology = self.request.query_params.get('technology')
        if operator:
            qs = qs.filter(operator_id=operator)
        if technology:
            qs = qs.filter(technology=technology)
        return qs


class SessionListView(generics.ListAPIView):
    serializer_class = DriveTestSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = DriveTestSession.objects.select_related(
            'operator', 'uploaded_by'
        ).order_by('-test_date', '-created_at')
        operator = self.request.query_params.get('operator')
        status = self.request.query_params.get('status')
        if operator:
            qs = qs.filter(operator_id=operator)
        if status:
            qs = qs.filter(status=status)
        return qs


class SessionDetailView(generics.RetrieveAPIView):
    serializer_class = DriveTestSessionSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = DriveTestSession.objects.select_related('operator', 'uploaded_by')
    lookup_field = 'session_ref'


class FileListView(generics.ListAPIView):
    serializer_class = DriveTestFileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = DriveTestFile.objects.select_related('session').order_by('-uploaded_at')
        session_ref = self.request.query_params.get('session')
        if session_ref:
            qs = qs.filter(session__session_ref=session_ref)
        return qs


class FindingListView(generics.ListAPIView):
    serializer_class = FindingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Finding.objects.select_related('session', 'cell').order_by('-created_at')
        session_ref = self.request.query_params.get('session')
        severity = self.request.query_params.get('severity')
        resolved = self.request.query_params.get('resolved')
        if session_ref:
            qs = qs.filter(session__session_ref=session_ref)
        if severity:
            qs = qs.filter(severity=severity)
        if resolved is not None:
            qs = qs.filter(is_resolved=resolved.lower() in ('true', '1', 'yes'))
        return qs


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def stats(request):
    """Quick summary counts for the drive test module."""
    from django.db.models import Count, Q
    data = {
        'sessions': DriveTestSession.objects.count(),
        'sessions_completed': DriveTestSession.objects.filter(status='COMPLETED').count(),
        'sessions_failed': DriveTestSession.objects.filter(status='FAILED').count(),
        'files': DriveTestFile.objects.count(),
        'cells': Cell.objects.count(),
        'sites': Site.objects.count(),
        'open_findings': Finding.objects.filter(is_resolved=False).count(),
        'critical_findings': Finding.objects.filter(is_resolved=False, severity='CRITICAL').count(),
    }
    return Response(data)
