"""Dashboard views package — split from monolithic views.py."""
# Re-export all view functions so urls.py continues to work unchanged.

from dashboard.views._common import RAT_TYPE_NAMES  # noqa: F401
from dashboard.views.dashboard import (  # noqa: F401
    system_health_api,
    dashboard_chart_data_api,
    index,
    set_active_operator,
    run_collection,
)
from dashboard.views.processing import (  # noqa: F401
    processing_summary_api,
    dashboard_kpis_api,
    processing_queue,
    processing_queue_api,
    stop_all_processing,
    pipeline_api,
)
from dashboard.views.cdr_search import (  # noqa: F401
    cdr_search,
    cdr_search_api,
    cdr_export,
    cdr_detail,
)
from dashboard.views.subscriber import (  # noqa: F401
    subscriber_view,
    subscriber_api,
)
from dashboard.views.file_registry import (  # noqa: F401
    file_registry,
    file_registry_reprocess,
    file_registry_export,
    file_download,
    file_registry_api,
)
from dashboard.views.analytics import (  # noqa: F401
    analytics_view,
    analytics_api,
    traffic_matrix_view,
    traffic_matrix_api,
)
from dashboard.views.duplicates import (  # noqa: F401
    duplicates_view,
    duplicates_api,
    duplicates_delete,
)
from dashboard.views.errors import (  # noqa: F401
    errors_view,
    errors_api,
)
from dashboard.views.activity_log import (  # noqa: F401
    activity_log_view,
    activity_log_api,
    activity_log_clear,
)
from dashboard.views.reports import (  # noqa: F401
    reports_view,
    reports_api,
    reports_export,
)
from dashboard.views.services import (  # noqa: F401
    services_view,
    services_api,
    services_action,
    intake_toggle,
)
from dashboard.views.monitoring import (  # noqa: F401
    system_monitoring_view,
    system_monitoring_overview_api,
    system_monitoring_timeseries_api,
    system_monitoring_error_detail_api,
    flow_monitoring_summary_api,
    flow_collection_api,
    flow_processing_api,
    flow_distribution_api,
    flow_reconciliation_api,
    backlog_api,
    storage_api,
    active_alarms_api,
)
