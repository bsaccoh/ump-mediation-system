from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render

from drive_test.access import dt_view_required
from drive_test.models import Campaign
from drive_test.services import comparison

_BY = {
    'operator': comparison.operator_comparison,
    'technology': comparison.technology_comparison,
    'campaign': comparison.campaign_comparison,
}


@login_required
@dt_view_required
def comparison_view(request):
    campaigns = (
        Campaign.objects.select_related('project', 'operator')
        .annotate(sample_count=Count('samples')).filter(sample_count__gt=0)
        .order_by('-created_at')
    )
    by = request.GET.get('by', 'operator')
    if by not in _BY:
        by = 'operator'
    selected = [int(x) for x in request.GET.getlist('campaign') if x.isdigit()]

    table = None
    if selected:
        table = _BY[by](selected)

    return render(request, 'drive_test/comparison.html', {
        'campaigns': campaigns, 'by': by, 'selected': selected, 'table': table,
        'dimensions_label': {'operator': 'Operator', 'technology': 'Technology',
                             'campaign': 'Campaign'}[by],
    })
