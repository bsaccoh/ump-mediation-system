from .dashboard import dashboard, placeholder
from .projects import (
    project_list, project_detail, project_create, project_edit,
)
from .campaigns import (
    campaign_list, campaign_detail, campaign_create, campaign_edit,
)
from .files import file_list, file_upload, file_delete, file_process
from .processing import processing_monitor, file_status
from .analysis import map_index, map_analysis

__all__ = [
    'dashboard', 'placeholder',
    'project_list', 'project_detail', 'project_create', 'project_edit',
    'campaign_list', 'campaign_detail', 'campaign_create', 'campaign_edit',
    'file_list', 'file_upload', 'file_delete', 'file_process',
    'processing_monitor', 'file_status',
    'map_index', 'map_analysis',
]
