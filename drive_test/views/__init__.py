from .dashboard import dashboard, placeholder
from .projects import (
    project_list, project_detail, project_create, project_edit,
)
from .campaigns import (
    campaign_list, campaign_detail, campaign_create, campaign_edit,
)
from .files import file_list, file_upload, file_delete

__all__ = [
    'dashboard', 'placeholder',
    'project_list', 'project_detail', 'project_create', 'project_edit',
    'campaign_list', 'campaign_detail', 'campaign_create', 'campaign_edit',
    'file_list', 'file_upload', 'file_delete',
]
